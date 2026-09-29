"""사용자 API. 사용자에게는 좌석 3상태(빈자리/예약(사용중)/사용불가)만 보이고, 대조 결과는 노출하지 않는다."""
import hmac
import sqlite3

from flask import Blueprint, current_app, g, jsonify, request

from auth import login_required
from db import get_db, get_settings, load_layout, tx
from routes import ApiError, int_field, json_body, now_ts
from service import (extend_check, refresh, reservation_json, set_seat_state, suspension,
                     user_active_reservation)
from status import user_view
from timeutil import to_iso

bp = Blueprint("api_user", __name__, url_prefix="/api")

CALL_DEDUP_SEC = 60  # 같은 사용자 60초 내 중복 호출은 기존 알림 반환
MEMO_MAX = 200


def _token_ok(given, seat):
    return bool(given) and isinstance(given, str) and hmac.compare_digest(given, seat["qr_token"])


def _seat_view(item, my_res):
    res = item["res"]
    if my_res and res and res["id"] == my_res["id"]:
        return "mine"
    return user_view(item["j"].seat_state)


def _policy(s):
    return {"default_use_min": s.default_use_min, "checkin_limit_min": s.checkin_limit_min,
            "extend_window_min": s.extend_window_min, "extend_min": s.extend_min, "max_extends": s.max_extends}


def _me(db, now):
    u = db.execute("SELECT warnings, suspended_until FROM users WHERE id=?", (g.user["id"],)).fetchone()
    return {"name": g.user["name"], "warnings": u["warnings"], "suspended_until": to_iso(suspension(u, now))}


def _find(results, seat_no):
    for it in results:
        if it["seat"]["no"] == seat_no:
            return it
    raise ApiError(404, "NOT_FOUND", "좌석을 찾을 수 없습니다.")


def _own_reservation(db, res_id):
    r = db.execute("SELECT r.*, s.label AS seat_label, s.qr_token FROM reservations r "
                   "JOIN seats s ON s.no = r.seat_no WHERE r.id = ?", (res_id,)).fetchone()
    if r is None or r["user_id"] != g.user["id"]:
        raise ApiError(404, "NOT_FOUND", "예약을 찾을 수 없습니다.")
    return dict(r)


# ---------------------------------------------------------------- 조회

@bp.get("/seats")
@login_required
def seats():
    db = get_db()
    now = now_ts()
    results = refresh(db, now)
    s = get_settings(db)
    my = user_active_reservation(db, g.user["id"])
    layout = load_layout(current_app.config["SEATS_FILE"])
    out = [{
        "no": it["seat"]["no"], "label": it["seat"]["label"], "x": it["seat"]["x"], "y": it["seat"]["y"],
        "zone": it["seat"]["zone"], "view": _seat_view(it, my),
    } for it in results]
    return jsonify({
        "server_time": to_iso(now), "grid": layout["grid"], "fixtures": layout["fixtures"],
        "seats": out, "my_reservation": reservation_json(my, now, s), "policy": _policy(s), "me": _me(db, now),
    })


@bp.get("/seats/<int:no>")
@login_required
def seat_detail(no):
    db = get_db()
    now = now_ts()
    it = _find(refresh(db, now), no)
    s = get_settings(db)
    my = user_active_reservation(db, g.user["id"])
    seat, res = it["seat"], it["res"]
    token = request.args.get("t")

    if res and my and res["id"] == my["id"]:
        mode = "mine_checkin" if res["status"] == "reserved" else "mine_in_use"
    elif res:
        mode = "reserved_by_other"
    elif seat["state"] == "unavailable":
        mode = "unavailable"
    else:
        mode = "reserve_now"

    return jsonify({
        "server_time": to_iso(now),
        "no": seat["no"], "label": seat["label"], "zone": seat["zone"],
        "view": _seat_view(it, my),
        "page_mode": mode,
        "unavailable": seat["state"] == "unavailable",
        "unavailable_note": seat["state_note"] if seat["state"] == "unavailable" else None,
        "occupied": res is None and seat["state"] == "occupied",  # 누군가 앉아 있음(대개 QR을 찍은 본인)
        "qr_ok": None if not token else _token_ok(token, seat),
        "my_reservation": reservation_json(my, now, s),
        "policy": _policy(s),
        "me": _me(db, now),
    })


# ---------------------------------------------------------------- 예약

@bp.post("/reservations")
@login_required
def create_reservation():
    body = json_body()
    seat_no = int_field(body, "seat_no")
    qr_token = body.get("qr_token") or None
    db = get_db()
    now = now_ts()
    with tx(db):
        it = _find(refresh(db, now), seat_no)
        seat = it["seat"]
        me = db.execute("SELECT suspended_until FROM users WHERE id=?", (g.user["id"],)).fetchone()
        until = suspension(me, now)
        if until:
            raise ApiError(403, "SUSPENDED", f"이용 정지 중입니다. ({to_iso(until)[:16].replace('T', ' ')}까지)")
        if user_active_reservation(db, g.user["id"]):
            raise ApiError(409, "ALREADY_HAS_RESERVATION", "이미 이용 중인 예약이 있습니다. 반납 후 다시 예약해 주세요.")
        if it["res"]:
            raise ApiError(409, "SEAT_TAKEN", "이미 예약된 좌석입니다.")
        if seat["state"] == "unavailable":
            raise ApiError(409, "SEAT_UNAVAILABLE", "사용할 수 없는 좌석입니다.")
        token_ok = _token_ok(qr_token, seat)
        if seat["state"] == "occupied" and not token_ok:
            raise ApiError(409, "SEAT_OCCUPIED", "현재 다른 이용자가 앉아 있는 좌석입니다.")
        if qr_token and not token_ok:
            raise ApiError(403, "BAD_QR_TOKEN", "좌석 QR을 다시 스캔해 주세요.")

        s = get_settings(db)
        status, source, checked_in = ("in_use", "seat_page", now) if token_ok else ("reserved", "map", None)
        try:
            cur = db.execute(
                "INSERT INTO reservations(user_id, seat_no, status, start_at, end_at, checked_in_at, source) "
                "VALUES (?,?,?,?,?,?,?)",
                (g.user["id"], seat_no, status, now, now + s.default_use_min * 60, checked_in, source),
            )
        except sqlite3.IntegrityError:
            raise ApiError(409, "SEAT_TAKEN", "방금 다른 이용자가 예약했습니다.")
        if token_ok:
            # SPEC-ASSUMPTION: 좌석 QR 체크인은 본인이 좌석에 있다는 뜻이므로 현장 상태를 '사용중'으로 둔다.
            set_seat_state(db, seat_no, "occupied", "checkin", now)
        refresh(db, now)
        res = _own_reservation(db, cur.lastrowid)
        return jsonify(reservation_json(res, now, s)), 201


@bp.post("/reservations/<int:res_id>/checkin")
@login_required
def checkin(res_id):
    body = json_body()
    db = get_db()
    now = now_ts()
    with tx(db):
        refresh(db, now)
        r = _own_reservation(db, res_id)
        if not _token_ok(body.get("qr_token"), r):
            raise ApiError(403, "BAD_QR_TOKEN", "좌석 QR을 다시 스캔해 주세요.")
        if r["status"] != "reserved":
            raise ApiError(409, "INVALID_STATE", "체크인할 수 있는 예약이 아닙니다.")
        seat = db.execute("SELECT state FROM seats WHERE no=?", (r["seat_no"],)).fetchone()
        if seat["state"] == "unavailable":
            raise ApiError(409, "SEAT_UNAVAILABLE", "사용할 수 없는 좌석입니다. 관리자에게 좌석 이동을 요청해 주세요.")
        db.execute("UPDATE reservations SET status='in_use', checked_in_at=? WHERE id=?", (now, res_id))
        set_seat_state(db, r["seat_no"], "occupied", "checkin", now)
        refresh(db, now)
        return jsonify(reservation_json(_own_reservation(db, res_id), now, get_settings(db)))


@bp.post("/reservations/<int:res_id>/extend")
@login_required
def extend(res_id):
    db = get_db()
    now = now_ts()
    with tx(db):
        refresh(db, now)
        r = _own_reservation(db, res_id)
        s = get_settings(db)
        ok, reason = extend_check(r, now, s)
        if not ok:
            raise ApiError(409, "EXTEND_NOT_ALLOWED", reason)
        db.execute("UPDATE reservations SET end_at = end_at + ?, extend_count = extend_count + 1 WHERE id=?",
                   (s.extend_min * 60, res_id))
        refresh(db, now)
        return jsonify(reservation_json(_own_reservation(db, res_id), now, s))


@bp.post("/reservations/<int:res_id>/return")
@login_required
def return_reservation(res_id):
    db = get_db()
    now = now_ts()
    with tx(db):
        refresh(db, now)
        r = _own_reservation(db, res_id)
        if r["status"] not in ("reserved", "in_use"):
            raise ApiError(409, "INVALID_STATE", "이미 종료된 예약입니다.")
        new_status = "cancelled" if r["status"] == "reserved" else "returned"
        db.execute("UPDATE reservations SET status=?, ended_at=? WHERE id=?", (new_status, now, res_id))
        if new_status == "returned":
            # SPEC-ASSUMPTION: 반납은 자리를 정리하고 떠난다는 뜻. 카메라가 붙어 있으면 다음 감지로 바로잡힌다.
            seat = db.execute("SELECT state FROM seats WHERE no=?", (r["seat_no"],)).fetchone()
            if seat["state"] == "occupied":
                set_seat_state(db, r["seat_no"], "empty", "return", now)
        refresh(db, now)
    return jsonify({"ok": True, "status": new_status})


# ---------------------------------------------------------------- 관리자 호출

@bp.post("/calls")
@login_required
def create_call():
    body = json_body()
    seat_no = int_field(body, "seat_no")
    memo = body.get("memo") or ""
    if not isinstance(memo, str):
        raise ApiError(400, "BAD_REQUEST", "'memo'는 문자열이어야 합니다.")
    memo = memo.strip()[:MEMO_MAX] or None
    db = get_db()
    now = now_ts()
    with tx(db):
        seat = db.execute("SELECT no FROM seats WHERE no=? AND active=1", (seat_no,)).fetchone()
        if seat is None:
            raise ApiError(404, "NOT_FOUND", "좌석을 찾을 수 없습니다.")
        dup = db.execute(
            "SELECT * FROM alerts WHERE type='call' AND created_by=? AND created_at > ? ORDER BY id DESC LIMIT 1",
            (g.user["id"], now - CALL_DEDUP_SEC),
        ).fetchone()
        if dup:
            return jsonify({"ok": True, "duplicate": True, "alert": _call_json(dup)}), 200
        res = db.execute("SELECT id FROM reservations WHERE seat_no=? AND status IN ('reserved','in_use')",
                         (seat_no,)).fetchone()
        cur = db.execute(
            "INSERT INTO alerts(seat_no, type, reservation_id, memo, created_at, created_by) VALUES (?, 'call', ?, ?, ?, ?)",
            (seat_no, res["id"] if res else None, memo, now, g.user["id"]),
        )
        row = db.execute("SELECT * FROM alerts WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "duplicate": False, "alert": _call_json(row)}), 201


def _call_json(a):
    return {"id": a["id"], "seat_no": a["seat_no"], "memo": a["memo"], "created_at": to_iso(a["created_at"])}
