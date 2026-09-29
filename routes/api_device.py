"""디바이스(Pi) → 웹: POST /api/detections (§7.1)."""
import hmac

from flask import Blueprint, current_app, jsonify, request

from db import get_db, tx
from routes import ApiError, int_field, json_body, now_ts
from service import refresh
from timeutil import parse_iso, to_iso

bp = Blueprint("api_device", __name__, url_prefix="/api")

OCCUPANCIES = ("person", "item", "empty")
# 합의된 계약: 서버-Pi 시계 차이가 이 값(초)을 넘으면 since를 보정한다.
CLOCK_SKEW_TOLERANCE_SEC = 5


@bp.post("/detections")
def post_detections():
    key = request.headers.get("X-Device-Key", "")
    if not key or not hmac.compare_digest(key, current_app.config["DEVICE_KEY"]):
        raise ApiError(403, "BAD_DEVICE_KEY", "디바이스 키가 올바르지 않습니다.")

    now = now_ts()
    body = json_body()
    items = body.get("seats")
    if not isinstance(items, list):
        raise ApiError(400, "BAD_REQUEST", "'seats' 필드는 배열이어야 합니다.")
    camera_id = body.get("camera_id")

    offset = 0
    if body.get("ts"):
        try:
            offset = now - parse_iso(body["ts"])
        except (ValueError, TypeError):
            raise ApiError(400, "BAD_REQUEST", "'ts' 시각 형식이 올바르지 않습니다.")
        if abs(offset) <= CLOCK_SKEW_TOLERANCE_SEC:
            offset = 0

    db = get_db()
    accepted, ignored = 0, []
    with tx(db):
        valid = {r["no"] for r in db.execute("SELECT no FROM seats WHERE active = 1")}
        for it in items:
            if not isinstance(it, dict):
                raise ApiError(400, "BAD_REQUEST", "'seats' 항목은 객체여야 합니다.")
            seat_no = int_field(it, "seat_no")
            occ = it.get("occupancy")
            if occ not in OCCUPANCIES:
                raise ApiError(400, "BAD_REQUEST", f"좌석 {seat_no}: occupancy는 person|item|empty 중 하나여야 합니다.")
            if seat_no not in valid:
                ignored.append(seat_no)
                continue
            if it.get("since"):
                try:
                    since = parse_iso(it["since"]) + offset
                except (ValueError, TypeError):
                    raise ApiError(400, "BAD_REQUEST", f"좌석 {seat_no}: 'since' 시각 형식이 올바르지 않습니다.")
            else:
                # SPEC-ASSUMPTION: since가 없으면 같은 상태의 기존 since를 유지, 상태가 바뀌었으면 지금으로.
                prev = db.execute("SELECT occupancy, since FROM detections WHERE seat_no = ?", (seat_no,)).fetchone()
                since = prev["since"] if prev and prev["occupancy"] == occ else now
            since = min(since, now)
            conf = it.get("confidence")
            conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
            db.execute(
                """INSERT INTO detections(seat_no, occupancy, since, confidence, camera_id, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(seat_no) DO UPDATE SET occupancy=excluded.occupancy, since=excluded.since,
                     confidence=excluded.confidence, camera_id=excluded.camera_id, updated_at=excluded.updated_at""",
                (seat_no, occ, since, conf, it.get("camera_id") or camera_id, now),
            )
            accepted += 1
        refresh(db, now)

    return jsonify({"ok": True, "accepted": accepted, "ignored": ignored, "server_time": to_iso(now)})
