"""관리자 API: 좌석·대조 현황, 확인 필요 알림, 상황별 조치, 이용자 경고·정지, 처리 이력, 설정, 통계."""
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, current_app, g, jsonify, request

from auth import admin_required
from config import tz
from db import get_db, get_settings, load_layout, tx
from routes import ApiError, int_field, json_body, now_ts
from service import log_admin, refresh, set_seat_state, setup_demo, suspension
from status import (ACTUAL_STATES, ALERT_TYPE_LABELS, DEFAULT_SETTINGS, OK, SEAT_STATES, SETTINGS_META,
                    SITUATIONS)
from timeutil import to_iso

bp = Blueprint("api_admin", __name__, url_prefix="/api/admin")

MEMO_MAX = 200
MAX_SUSPEND_DAYS = 90
ACTION_LABELS = {
    "resolve": "처리 완료", "seat_state": "현장 상태 변경", "assign": "대리 예약", "checkin": "대리 체크인",
    "move": "좌석 이동", "extend": "관리자 연장", "force_return": "강제 반납", "warn": "경고 부여",
    "unwarn": "경고 취소", "suspend": "이용 정지", "unsuspend": "정지 해제", "demo": "시연 상황 배치",
    "settings": "설정 변경", "admin_on": "관리자 모드 시작", "admin_off": "관리자 모드 종료",
    "admin_locked": "관리자 코드 잠금",
}
RES_STATUS = {"reserved": "예약(입실 전)", "in_use": "이용 중"}


def _memo(body, key="memo"):
    v = body.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ApiError(400, "BAD_REQUEST", f"'{key}'는 문자열이어야 합니다.")
    return v.strip()[:MEMO_MAX] or None


def _user_summary(u, now, s):
    until = suspension(u, now)
    return {"id": u["id"], "name": u["name"], "student_no": u["student_no"], "warnings": u["warnings"],
            "suspended_until": to_iso(until), "suspend_suggested": u["warnings"] >= s.warning_limit and not until}


def _res_summary(r, now, s):
    if not r:
        return None
    return {
        "id": r["id"], "status": r["status"], "status_label": RES_STATUS.get(r["status"], r["status"]),
        "start_at": to_iso(r["start_at"]), "end_at": to_iso(r["end_at"]), "checked_in_at": to_iso(r["checked_in_at"]),
        "remaining_sec": max(0, r["end_at"] - now), "source": r["source"],
        "user": _user_summary({"id": r["user_id"], "name": r["user_name"], "student_no": r["student_no"],
                               "warnings": r["user_warnings"], "suspended_until": r["user_suspended_until"]}, now, s),
    }


def _get_reservation(db, res_id, active=True):
    r = db.execute("SELECT * FROM reservations WHERE id=?", (res_id,)).fetchone()
    if r is None:
        raise ApiError(404, "NOT_FOUND", "예약을 찾을 수 없습니다.")
    if active and r["status"] not in ("reserved", "in_use"):
        raise ApiError(409, "INVALID_STATE", "이미 종료된 예약입니다.")
    return r


def _get_user(db, user_id):
    u = db.execute("SELECT * FROM users WHERE id=? AND role='user'", (user_id,)).fetchone()
    if u is None:
        raise ApiError(404, "NOT_FOUND", "이용자를 찾을 수 없습니다.")
    return u


def _get_seat(db, seat_no):
    seat = db.execute("SELECT * FROM seats WHERE no=? AND active=1", (seat_no,)).fetchone()
    if seat is None:
        raise ApiError(404, "NOT_FOUND", "좌석을 찾을 수 없습니다.")
    return seat


def _resolve_seat_alerts(db, seat_no, now, resolution, types=None):
    sql = "UPDATE alerts SET resolved_at=?, resolved_by=?, resolution=? WHERE seat_no=? AND resolved_at IS NULL"
    args = [now, g.user["id"], resolution, seat_no]
    if types:
        sql += f" AND type IN ({','.join('?' * len(types))})"
        args += list(types)
    db.execute(sql, args)


def _ok(**extra):
    return jsonify({"ok": True, **extra})


# ---------------------------------------------------------------- 현황

@bp.get("/seats")
@admin_required
def seats():
    db = get_db()
    now = now_ts()
    results = refresh(db, now)
    s = get_settings(db)
    layout = load_layout(current_app.config["SEATS_FILE"])
    open_alerts = {}
    for a in db.execute("SELECT id, seat_no, type FROM alerts WHERE resolved_at IS NULL AND type <> 'call'"):
        open_alerts.setdefault(a["seat_no"], {})[a["type"]] = a["id"]
    summary = {k: 0 for k in SEAT_STATES}
    summary["issues"] = 0
    out = []
    for it in results:
        seat, j, res = it["seat"], it["j"], it["res"]
        summary[j.seat_state] += 1
        if j.situation != OK:
            summary["issues"] += 1
        out.append({
            "no": seat["no"], "label": seat["label"], "x": seat["x"], "y": seat["y"], "zone": seat["zone"],
            "seat_state": j.seat_state, "seat_state_label": SEAT_STATES[j.seat_state],
            "situation": j.situation, "situation_label": SITUATIONS[j.situation]["label"],
            "situation_desc": SITUATIONS[j.situation]["desc"], "note": j.note,
            "since": to_iso(j.since), "elapsed_sec": max(0, now - j.since),
            "deadline": to_iso(j.deadline), "deadline_sec": (j.deadline - now) if j.deadline else None,
            "actual": seat["state"], "actual_label": ACTUAL_STATES[seat["state"]],
            "actual_since": to_iso(seat["state_since"]), "actual_elapsed_sec": max(0, now - seat["state_since"]),
            "actual_source": seat["state_source"], "actual_note": seat["state_note"],
            "reservation": _res_summary(res, now, s),
            "alert_id": open_alerts.get(seat["no"], {}).get(j.situation),
        })
    return jsonify({
        "server_time": to_iso(now), "grid": layout["grid"], "fixtures": layout["fixtures"],
        "summary": summary, "seats": out, "settings": s.as_dict(),
    })


@bp.get("/alerts")
@admin_required
def alerts():
    db = get_db()
    now = now_ts()
    refresh(db, now)
    s = get_settings(db)
    open_only = request.args.get("open") in ("1", "true")
    sql = """SELECT a.*, s.label AS seat_label, s.state AS seat_actual,
                    r.id AS r_id, r.status AS r_status, r.start_at AS r_start, r.end_at AS r_end,
                    r.checked_in_at AS r_checkin, r.user_id AS r_uid,
                    ru.name AS r_name, ru.student_no AS r_sno, ru.warnings AS r_warn, ru.suspended_until AS r_susp,
                    cu.name AS caller_name, cu.student_no AS caller_sno,
                    ar.id AS active_res_id, ar.status AS active_res_status
               FROM alerts a
               JOIN seats s ON s.no = a.seat_no
               LEFT JOIN reservations r ON r.id = a.reservation_id
               LEFT JOIN users ru ON ru.id = r.user_id
               LEFT JOIN users cu ON cu.id = a.created_by
               LEFT JOIN reservations ar ON ar.seat_no = a.seat_no AND ar.status IN ('reserved','in_use')"""
    if open_only:
        sql += " WHERE a.resolved_at IS NULL ORDER BY a.created_at ASC, a.id ASC"
    else:
        sql += " ORDER BY a.created_at DESC, a.id DESC LIMIT 200"
    out = []
    for a in db.execute(sql).fetchall():
        out.append({
            "id": a["id"], "seat_no": a["seat_no"], "seat_label": a["seat_label"], "seat_actual": a["seat_actual"],
            "type": a["type"], "type_label": ALERT_TYPE_LABELS[a["type"]],
            "desc": SITUATIONS.get(a["type"], {}).get("desc", ""),
            "created_at": to_iso(a["created_at"]), "elapsed_sec": max(0, now - a["created_at"]),
            "memo": a["memo"],
            "caller": {"name": a["caller_name"], "student_no": a["caller_sno"]} if a["caller_name"] else None,
            "reservation": {
                "id": a["r_id"], "status": a["r_status"], "start_at": to_iso(a["r_start"]), "end_at": to_iso(a["r_end"]),
                "checked_in_at": to_iso(a["r_checkin"]),
                "user": _user_summary({"id": a["r_uid"], "name": a["r_name"], "student_no": a["r_sno"],
                                       "warnings": a["r_warn"], "suspended_until": a["r_susp"]}, now, s),
            } if a["r_id"] else None,
            "active_reservation_id": a["active_res_id"], "active_reservation_status": a["active_res_status"],
            "resolved_at": to_iso(a["resolved_at"]), "resolution": a["resolution"],
        })
    return jsonify({"server_time": to_iso(now), "alerts": out})


@bp.post("/alerts/<int:alert_id>/resolve")
@admin_required
def resolve_alert(alert_id):
    body = json_body()
    db = get_db()
    now = now_ts()
    with tx(db):
        a = db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if a is None:
            raise ApiError(404, "NOT_FOUND", "알림을 찾을 수 없습니다.")
        if a["resolved_at"] is not None:
            raise ApiError(409, "INVALID_STATE", "이미 처리된 알림입니다.")
        db.execute("UPDATE alerts SET resolved_at=?, resolved_by=?, resolution='handled' WHERE id=?",
                   (now, g.user["id"], alert_id))
        log_admin(db, g.user["id"], "resolve", now, seat_no=a["seat_no"], reservation_id=a["reservation_id"],
                  alert_id=alert_id, memo=_memo(body) or ALERT_TYPE_LABELS[a["type"]])
    return _ok()


# ---------------------------------------------------------------- 좌석 현장 상태 (임시 배분)

@bp.post("/seats/<int:no>/state")
@admin_required
def change_seat_state(no):
    body = json_body()
    state = body.get("state")
    if state not in ACTUAL_STATES:
        raise ApiError(400, "BAD_REQUEST", "state는 empty|occupied|item|unavailable 중 하나여야 합니다.")
    note = _memo(body, "note")
    db = get_db()
    now = now_ts()
    with tx(db):
        _get_seat(db, no)
        set_seat_state(db, no, state, "manual", now, note=note)
        log_admin(db, g.user["id"], "seat_state", now, seat_no=no,
                  memo=ACTUAL_STATES[state] + (f" ({note})" if note else ""))
        refresh(db, now)
    return _ok()


# ---------------------------------------------------------------- 예약 조치

@bp.post("/reservations")
@admin_required
def assign():
    """대리 예약 / 현장 배정. checkin=true면 바로 이용 중(착석한 이용자에게 좌석 배정)."""
    body = json_body()
    user_id = int_field(body, "user_id")
    seat_no = int_field(body, "seat_no")
    checkin = bool(body.get("checkin"))
    db = get_db()
    now = now_ts()
    with tx(db):
        refresh(db, now)
        s = get_settings(db)
        u = _get_user(db, user_id)
        seat = _get_seat(db, seat_no)
        if suspension(u, now):
            raise ApiError(409, "SUSPENDED", f"{u['name']} 님은 이용 정지 중입니다. 정지를 해제한 뒤 배정하세요.")
        if db.execute("SELECT 1 FROM reservations WHERE user_id=? AND status IN ('reserved','in_use')", (user_id,)).fetchone():
            raise ApiError(409, "ALREADY_HAS_RESERVATION", f"{u['name']} 님은 이미 다른 예약이 있습니다.")
        if db.execute("SELECT 1 FROM reservations WHERE seat_no=? AND status IN ('reserved','in_use')", (seat_no,)).fetchone():
            raise ApiError(409, "SEAT_TAKEN", "이미 예약된 좌석입니다.")
        if seat["state"] == "unavailable":
            raise ApiError(409, "SEAT_UNAVAILABLE", "사용불가 좌석입니다. 현장 상태를 먼저 바꾸세요.")
        try:
            cur = db.execute(
                "INSERT INTO reservations(user_id, seat_no, status, start_at, end_at, checked_in_at, source) "
                "VALUES (?,?,?,?,?,?,'admin')",
                (user_id, seat_no, "in_use" if checkin else "reserved", now, now + s.default_use_min * 60,
                 now if checkin else None),
            )
        except sqlite3.IntegrityError:
            raise ApiError(409, "SEAT_TAKEN", "방금 다른 예약이 생겼습니다.")
        if checkin:
            set_seat_state(db, seat_no, "occupied", "manual", now)
        log_admin(db, g.user["id"], "assign", now, seat_no=seat_no, reservation_id=cur.lastrowid,
                  target_user_id=user_id, memo=("바로 이용 시작" if checkin else "입실 전 예약") +
                  (f" · {_memo(body)}" if _memo(body) else ""))
        refresh(db, now)
    return _ok(reservation_id=cur.lastrowid), 201


@bp.post("/reservations/<int:res_id>/checkin")
@admin_required
def admin_checkin(res_id):
    db = get_db()
    now = now_ts()
    with tx(db):
        refresh(db, now)
        r = _get_reservation(db, res_id)
        if r["status"] != "reserved":
            raise ApiError(409, "INVALID_STATE", "체크인 전 예약만 대리 체크인할 수 있습니다.")
        seat = _get_seat(db, r["seat_no"])
        if seat["state"] == "unavailable":
            raise ApiError(409, "SEAT_UNAVAILABLE", "사용불가 좌석입니다. 좌석을 먼저 이동하세요.")
        db.execute("UPDATE reservations SET status='in_use', checked_in_at=? WHERE id=?", (now, res_id))
        set_seat_state(db, r["seat_no"], "occupied", "manual", now)
        log_admin(db, g.user["id"], "checkin", now, seat_no=r["seat_no"], reservation_id=res_id,
                  target_user_id=r["user_id"])
        refresh(db, now)
    return _ok()


@bp.post("/reservations/<int:res_id>/move")
@admin_required
def move(res_id):
    body = json_body()
    to_no = int_field(body, "seat_no")
    db = get_db()
    now = now_ts()
    with tx(db):
        refresh(db, now)
        r = _get_reservation(db, res_id)
        if to_no == r["seat_no"]:
            raise ApiError(400, "BAD_REQUEST", "현재 좌석과 같은 좌석입니다.")
        target = _get_seat(db, to_no)
        if db.execute("SELECT 1 FROM reservations WHERE seat_no=? AND status IN ('reserved','in_use')", (to_no,)).fetchone():
            raise ApiError(409, "SEAT_TAKEN", "옮길 좌석에 이미 예약이 있습니다.")
        if target["state"] != "empty":
            raise ApiError(409, "SEAT_OCCUPIED" if target["state"] == "occupied" else "SEAT_UNAVAILABLE",
                           "빈자리로만 옮길 수 있습니다.")
        from_no = r["seat_no"]
        db.execute("UPDATE reservations SET seat_no=? WHERE id=?", (to_no, res_id))
        if r["status"] == "in_use":
            # 이용 중인 이용자가 자리를 옮기므로 새 좌석은 사용중, 기존 좌석은(사용불가가 아니면) 빈자리
            set_seat_state(db, to_no, "occupied", "manual", now)
            old = db.execute("SELECT state FROM seats WHERE no=?", (from_no,)).fetchone()
            if old["state"] == "occupied":
                set_seat_state(db, from_no, "empty", "manual", now)
        labels = dict(db.execute("SELECT no, label FROM seats WHERE no IN (?,?)", (from_no, to_no)).fetchall())
        log_admin(db, g.user["id"], "move", now, seat_no=to_no, reservation_id=res_id, target_user_id=r["user_id"],
                  memo=f"{labels[from_no]} → {labels[to_no]}" + (f" · {_memo(body)}" if _memo(body) else ""))
        refresh(db, now)
    return _ok()


@bp.post("/reservations/<int:res_id>/extend")
@admin_required
def admin_extend(res_id):
    db = get_db()
    now = now_ts()
    with tx(db):
        refresh(db, now)
        r = _get_reservation(db, res_id)
        s = get_settings(db)
        # 관리자 연장은 연장 창·횟수 제한을 적용하지 않고, 이용자 연장 횟수에도 포함하지 않는다.
        db.execute("UPDATE reservations SET end_at = end_at + ? WHERE id=?", (s.extend_min * 60, res_id))
        log_admin(db, g.user["id"], "extend", now, seat_no=r["seat_no"], reservation_id=res_id,
                  target_user_id=r["user_id"], memo=f"+{s.extend_min}분")
        refresh(db, now)
    return _ok()


@bp.post("/reservations/<int:res_id>/force-return")
@admin_required
def force_return(res_id):
    body = json_body()
    db = get_db()
    now = now_ts()
    with tx(db):
        r = _get_reservation(db, res_id)
        db.execute("UPDATE reservations SET status='force_returned', ended_at=? WHERE id=?", (now, res_id))
        _resolve_seat_alerts(db, r["seat_no"], now, "force_returned")
        log_admin(db, g.user["id"], "force_return", now, seat_no=r["seat_no"], reservation_id=res_id,
                  target_user_id=r["user_id"], memo=_memo(body))
        refresh(db, now)
    return _ok()


# ---------------------------------------------------------------- 이용자 관리 (경고·정지)

@bp.get("/users")
@admin_required
def users():
    db = get_db()
    now = now_ts()
    s = get_settings(db)
    rows = db.execute(
        """SELECT u.*, r.id AS r_id, r.status AS r_status, s.label AS r_seat
             FROM users u
             LEFT JOIN reservations r ON r.user_id = u.id AND r.status IN ('reserved','in_use')
             LEFT JOIN seats s ON s.no = r.seat_no
            WHERE u.role = 'user' ORDER BY u.student_no"""
    ).fetchall()
    out = []
    for u in rows:
        d = _user_summary(u, now, s)
        d["reservation"] = {"id": u["r_id"], "status": u["r_status"], "status_label": RES_STATUS[u["r_status"]],
                            "seat_label": u["r_seat"]} if u["r_id"] else None
        out.append(d)
    return jsonify({"users": out, "warning_limit": s.warning_limit, "suspend_days": s.suspend_days})


@bp.post("/users/<int:user_id>/warn")
@admin_required
def warn(user_id):
    body = json_body()
    alert_id = int_field(body, "alert_id", required=False)
    reason = _memo(body, "reason")
    db = get_db()
    now = now_ts()
    with tx(db):
        u = _get_user(db, user_id)
        seat_no = None
        if alert_id is not None:
            a = db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if a is None:
                raise ApiError(404, "NOT_FOUND", "알림을 찾을 수 없습니다.")
            seat_no = a["seat_no"]
            reason = reason or ALERT_TYPE_LABELS[a["type"]]
            if body.get("resolve") and a["resolved_at"] is None:
                db.execute("UPDATE alerts SET resolved_at=?, resolved_by=?, resolution='warned' WHERE id=?",
                           (now, g.user["id"], alert_id))
        db.execute("UPDATE users SET warnings = warnings + 1 WHERE id=?", (user_id,))
        log_admin(db, g.user["id"], "warn", now, seat_no=seat_no, target_user_id=user_id, alert_id=alert_id,
                  memo=reason)
        warnings = u["warnings"] + 1
    s = get_settings(db)
    return _ok(warnings=warnings, suspend_suggested=warnings >= s.warning_limit and not suspension(u, now))


@bp.post("/users/<int:user_id>/unwarn")
@admin_required
def unwarn(user_id):
    body = json_body()
    db = get_db()
    now = now_ts()
    with tx(db):
        u = _get_user(db, user_id)
        if u["warnings"] <= 0:
            raise ApiError(409, "INVALID_STATE", "취소할 경고가 없습니다.")
        db.execute("UPDATE users SET warnings = warnings - 1 WHERE id=?", (user_id,))
        log_admin(db, g.user["id"], "unwarn", now, target_user_id=user_id, memo=_memo(body, "reason"))
    return _ok(warnings=u["warnings"] - 1)


@bp.post("/users/<int:user_id>/suspend")
@admin_required
def suspend(user_id):
    body = json_body()
    db = get_db()
    now = now_ts()
    s = get_settings(db)
    days = int_field(body, "days", required=False) or s.suspend_days
    if not 1 <= days <= MAX_SUSPEND_DAYS:
        raise ApiError(400, "BAD_REQUEST", f"정지 기간은 1~{MAX_SUSPEND_DAYS}일이어야 합니다.")
    with tx(db):
        _get_user(db, user_id)
        until = now + days * 86400
        db.execute("UPDATE users SET suspended_until=? WHERE id=?", (until, user_id))
        log_admin(db, g.user["id"], "suspend", now, target_user_id=user_id,
                  memo=f"{days}일" + (f" · {_memo(body, 'reason')}" if _memo(body, "reason") else ""))
    return _ok(suspended_until=to_iso(until))


@bp.post("/users/<int:user_id>/unsuspend")
@admin_required
def unsuspend(user_id):
    db = get_db()
    now = now_ts()
    with tx(db):
        u = _get_user(db, user_id)
        if not suspension(u, now):
            raise ApiError(409, "INVALID_STATE", "정지 중인 이용자가 아닙니다.")
        db.execute("UPDATE users SET suspended_until=NULL WHERE id=?", (user_id,))
        log_admin(db, g.user["id"], "unsuspend", now, target_user_id=user_id)
    return _ok()


# ---------------------------------------------------------------- 처리 이력

@bp.get("/log")
@admin_required
def admin_log():
    db = get_db()
    limit = min(max(int(request.args.get("limit", 50) or 50), 1), 200)
    rows = db.execute(
        """SELECT l.*, a.name AS admin_name, u.name AS user_name, u.student_no AS user_sno, s.label AS seat_label
             FROM admin_log l
             LEFT JOIN users a ON a.id = l.admin_id
             LEFT JOIN users u ON u.id = l.target_user_id
             LEFT JOIN seats s ON s.no = l.seat_no
            ORDER BY l.at DESC, l.id DESC LIMIT ?""", (limit,)).fetchall()
    return jsonify({"log": [{
        "id": r["id"], "at": to_iso(r["at"]), "action": r["action"],
        "action_label": ACTION_LABELS.get(r["action"], r["action"]), "admin_name": r["admin_name"],
        "seat_label": r["seat_label"], "user_name": r["user_name"], "user_student_no": r["user_sno"],
        "memo": r["memo"],
    } for r in rows]})


# ---------------------------------------------------------------- 시연 상황 배치

@bp.post("/demo")
@admin_required
def demo():
    db = get_db()
    now = now_ts()
    msgs = setup_demo(db, now)
    with tx(db):
        log_admin(db, g.user["id"], "demo", now, memo=f"{len(msgs)}개 좌석 배치")
    return _ok(messages=msgs)


# ---------------------------------------------------------------- 설정

def _settings_json(s):
    return {
        "settings": s.as_dict(),
        "defaults": DEFAULT_SETTINGS,
        "meta": {k: {"label": m[0], "unit": m[1], "desc": m[2], "min": m[3], "max": m[4]} for k, m in SETTINGS_META.items()},
    }


@bp.get("/settings")
@admin_required
def get_settings_api():
    return jsonify(_settings_json(get_settings(get_db())))


@bp.put("/settings")
@admin_required
def put_settings():
    body = json_body()
    if not body:
        raise ApiError(400, "BAD_REQUEST", "변경할 설정이 없습니다.")
    clean = {}
    for k, v in body.items():
        if k not in SETTINGS_META:
            raise ApiError(400, "BAD_REQUEST", f"알 수 없는 설정 키: {k}")
        label, unit, _desc, lo, hi = SETTINGS_META[k]
        try:
            if isinstance(v, bool) or (isinstance(v, float) and not v.is_integer()):
                raise ValueError
            iv = int(v)
        except (TypeError, ValueError):
            raise ApiError(400, "BAD_REQUEST", f"'{label}' 값은 정수여야 합니다.")
        if not lo <= iv <= hi:
            raise ApiError(400, "BAD_REQUEST", f"'{label}' 값은 {lo}~{hi} {unit} 범위여야 합니다.")
        clean[k] = iv
    db = get_db()
    now = now_ts()
    with tx(db):
        for k, v in clean.items():
            db.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (k, str(v)))
        log_admin(db, g.user["id"], "settings", now,
                  memo=", ".join(f"{SETTINGS_META[k][0]}={v}" for k, v in clean.items()))
    return jsonify(_settings_json(get_settings(db)))


# ---------------------------------------------------------------- 통계

@bp.get("/stats")
@admin_required
def stats():
    zone = tz()
    now = now_ts()
    date_s = request.args.get("date")
    try:
        day = datetime.strptime(date_s, "%Y-%m-%d").date() if date_s else datetime.fromtimestamp(now, zone).date()
    except ValueError:
        raise ApiError(400, "BAD_REQUEST", "date는 YYYY-MM-DD 형식이어야 합니다.")
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    day_start, day_end = int(start.timestamp()), int((start + timedelta(days=1)).timestamp())
    return jsonify({"date": day.isoformat(), "hours": compute_hourly_stats(get_db(), day_start, day_end, now)})


def _bucket(seat_state, situation):
    if situation in ("away", "hoarding", "unauthorized"):
        return situation
    if seat_state == "in_use" and situation == OK:
        return "in_use"
    return None


def compute_hourly_stats(conn, day_start, day_end, now):
    """status_log 전이 이력을 구간으로 펼쳐 시간대별 좌석·분 누적을 계산한다."""
    secs = [{"in_use": 0, "away": 0, "hoarding": 0, "unauthorized": 0} for _ in range(24)]
    end_cap = min(day_end, now)
    for (seat_no,) in conn.execute("SELECT DISTINCT seat_no FROM status_log").fetchall():
        before = conn.execute(
            "SELECT seat_state, situation, at FROM status_log WHERE seat_no=? AND at < ? ORDER BY at DESC, id DESC LIMIT 1",
            (seat_no, day_start)).fetchone()
        logs = conn.execute(
            "SELECT seat_state, situation, at FROM status_log WHERE seat_no=? AND at >= ? AND at < ? ORDER BY at, id",
            (seat_no, day_start, end_cap)).fetchall()
        points = ([(before["seat_state"], before["situation"], day_start)] if before else []) + \
                 [(r["seat_state"], r["situation"], r["at"]) for r in logs]
        for i, (st, sit, t0) in enumerate(points):
            t1 = points[i + 1][2] if i + 1 < len(points) else end_cap
            key = _bucket(st, sit)
            t = t0
            while key and t < t1:
                h = (t - day_start) // 3600
                seg = min(t1, day_start + (h + 1) * 3600) - t
                if 0 <= h < 24:
                    secs[h][key] += seg
                t += seg
    hours = []
    for h in range(24):
        x = secs[h]
        base = x["in_use"] + x["away"] + x["hoarding"]
        hours.append({
            "hour": h,
            "in_use_min": round(x["in_use"] / 60, 1),
            "away_min": round(x["away"] / 60, 1),
            "hoarding_min": round(x["hoarding"] / 60, 1),
            "unauthorized_min": round(x["unauthorized"] / 60, 1),
            # 이탈·사석화 비율 = (이탈 + 사석화) / (정상 이용 + 이탈 + 사석화)
            "issue_rate": round((x["away"] + x["hoarding"]) / base, 3) if base else 0.0,
        })
    return hours
