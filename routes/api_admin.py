"""관리자 API (§7.3)."""
from datetime import datetime, timedelta

from flask import Blueprint, current_app, g, jsonify, request

from auth import admin_required
from db import get_db, get_settings, load_layout, tx
from routes import ApiError, json_body, now_ts
from service import refresh
from status import ALERT_TYPE_LABELS, DEFAULT_SETTINGS, SETTINGS_META, STATE_META, STATES, HOARDING, IN_USE, UNAUTHORIZED
from timeutil import to_iso
from config import tz

bp = Blueprint("api_admin", __name__, url_prefix="/api/admin")


def _res_summary(r):
    if not r:
        return None
    return {
        "id": r["id"], "user_name": r["user_name"], "student_no": r["student_no"], "status": r["status"],
        "start_at": to_iso(r["start_at"]), "end_at": to_iso(r["end_at"]), "checked_in_at": to_iso(r["checked_in_at"]),
    }


@bp.get("/seats")
@admin_required
def seats():
    db = get_db()
    now = now_ts()
    results = refresh(db, now)
    layout = load_layout(current_app.config["SEATS_FILE"])
    summary = {st: 0 for st in STATES}
    out = []
    for it in results:
        seat, j, res, det = it["seat"], it["j"], it["res"], it["det"]
        summary[j.state] += 1
        out.append({
            "no": seat["no"], "label": seat["label"], "x": seat["x"], "y": seat["y"], "zone": seat["zone"],
            "state": j.state, "state_label": STATE_META[j.state]["label"],
            "alert_type": STATE_META[j.state]["alert"],
            "since": to_iso(j.since), "elapsed_sec": j.elapsed_sec, "deadline": to_iso(j.deadline),
            "deadline_sec": (j.deadline - now) if j.deadline else None,
            "occupancy": det["occupancy"] if det else None,
            "confidence": det["confidence"] if det else None,
            "detection_updated_at": to_iso(det["updated_at"]) if det else None,
            "detection_age_sec": (now - det["updated_at"]) if det else None,
            "reservation": _res_summary(res),
        })
    return jsonify({
        "server_time": to_iso(now), "grid": layout["grid"], "fixtures": layout["fixtures"],
        "summary": summary, "state_labels": {k: v["label"] for k, v in STATE_META.items()}, "seats": out,
    })


@bp.get("/alerts")
@admin_required
def alerts():
    db = get_db()
    now = now_ts()
    refresh(db, now)
    open_only = request.args.get("open") in ("1", "true")
    sql = """SELECT a.*, s.label AS seat_label,
                    r.id AS r_id, r.status AS r_status, r.start_at AS r_start, r.end_at AS r_end,
                    r.checked_in_at AS r_checkin, ru.name AS r_name, ru.student_no AS r_sno,
                    cu.name AS caller_name, cu.student_no AS caller_sno,
                    ar.id AS active_res_id
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
            "id": a["id"], "seat_no": a["seat_no"], "seat_label": a["seat_label"],
            "type": a["type"], "type_label": ALERT_TYPE_LABELS[a["type"]],
            "created_at": to_iso(a["created_at"]), "elapsed_sec": max(0, now - a["created_at"]),
            "memo": a["memo"],
            "caller": {"name": a["caller_name"], "student_no": a["caller_sno"]} if a["caller_name"] else None,
            "reservation": {
                "id": a["r_id"], "user_name": a["r_name"], "student_no": a["r_sno"], "status": a["r_status"],
                "start_at": to_iso(a["r_start"]), "end_at": to_iso(a["r_end"]), "checked_in_at": to_iso(a["r_checkin"]),
            } if a["r_id"] else None,
            "active_reservation_id": a["active_res_id"],
            "resolved_at": to_iso(a["resolved_at"]), "resolution": a["resolution"],
        })
    return jsonify({"server_time": to_iso(now), "alerts": out})


@bp.post("/alerts/<int:alert_id>/resolve")
@admin_required
def resolve_alert(alert_id):
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
    return jsonify({"ok": True})


@bp.post("/reservations/<int:res_id>/force-return")
@admin_required
def force_return(res_id):
    db = get_db()
    now = now_ts()
    with tx(db):
        r = db.execute("SELECT * FROM reservations WHERE id = ?", (res_id,)).fetchone()
        if r is None:
            raise ApiError(404, "NOT_FOUND", "예약을 찾을 수 없습니다.")
        if r["status"] not in ("reserved", "in_use"):
            raise ApiError(409, "INVALID_STATE", "이미 종료된 예약입니다.")
        db.execute("UPDATE reservations SET status='force_returned', ended_at=? WHERE id=?", (now, res_id))
        db.execute(
            "UPDATE alerts SET resolved_at=?, resolved_by=?, resolution='force_returned' "
            "WHERE seat_no=? AND resolved_at IS NULL",
            (now, g.user["id"], r["seat_no"]),
        )
        refresh(db, now)
    return jsonify({"ok": True})


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
    with tx(db):
        for k, v in clean.items():
            db.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (k, str(v)))
    return jsonify(_settings_json(get_settings(db)))


# ---------------------------------------------------------------- 통계 (P2)

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
    day_start = int(datetime(day.year, day.month, day.day, tzinfo=zone).timestamp())
    day_end = int((datetime(day.year, day.month, day.day, tzinfo=zone) + timedelta(days=1)).timestamp())
    return jsonify({"date": day.isoformat(), "hours": compute_hourly_stats(get_db(), day_start, day_end, now)})


def compute_hourly_stats(conn, day_start, day_end, now):
    """status_log의 전이 이력을 구간으로 펼쳐 시간대별 좌석·분 누적을 계산한다."""
    tracked = {IN_USE: "in_use", HOARDING: "hoarding", UNAUTHORIZED: "unauthorized"}
    secs = [{"in_use": 0, "hoarding": 0, "unauthorized": 0} for _ in range(24)]
    end_cap = min(day_end, now)
    seat_nos = [r["seat_no"] for r in conn.execute("SELECT DISTINCT seat_no FROM status_log")]
    for seat_no in seat_nos:
        # 하루 시작 시점의 상태(직전 로그) + 당일 로그
        before = conn.execute(
            "SELECT state, at FROM status_log WHERE seat_no=? AND at < ? ORDER BY at DESC, id DESC LIMIT 1",
            (seat_no, day_start)).fetchone()
        logs = conn.execute(
            "SELECT state, at FROM status_log WHERE seat_no=? AND at >= ? AND at < ? ORDER BY at, id",
            (seat_no, day_start, end_cap)).fetchall()
        points = ([(before["state"], day_start)] if before else []) + [(r["state"], r["at"]) for r in logs]
        for i, (state, start) in enumerate(points):
            end = points[i + 1][1] if i + 1 < len(points) else end_cap
            key = tracked.get(state)
            if key is None or end <= start:
                continue
            # 구간을 시간대별로 분할
            t = start
            while t < end:
                h = (t - day_start) // 3600
                h_end = day_start + (h + 1) * 3600
                seg = min(end, h_end) - t
                if 0 <= h < 24:
                    secs[h][key] += seg
                t += seg
    hours = []
    for h in range(24):
        in_use = round(secs[h]["in_use"] / 60, 1)
        hoarding = round(secs[h]["hoarding"] / 60, 1)
        unauth = round(secs[h]["unauthorized"] / 60, 1)
        base = secs[h]["in_use"] + secs[h]["hoarding"]
        # SPEC-ASSUMPTION: 사석화율 = 사석화 시간 / (정상 이용 + 사석화 시간)
        rate = round(secs[h]["hoarding"] / base, 3) if base else 0.0
        hours.append({"hour": h, "in_use_min": in_use, "hoarding_min": hoarding,
                      "unauthorized_min": unauth, "hoarding_rate": rate})
    return hours
