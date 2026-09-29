"""디바이스(Pi) → 웹: POST /api/detections.

카메라 연동 전에는 관리자가 현장 상태를 임시로 배분한다. 카메라가 붙으면 이 API가 현장 상태를 갱신한다
(person·item → 사용중, empty → 빈자리). 관리자가 '사용불가'로 지정한 좌석은 카메라가 덮어쓰지 않는다.
"""
import hmac

from flask import Blueprint, current_app, jsonify, request

from db import get_db, tx
from routes import ApiError, int_field, json_body, now_ts
from service import refresh
from timeutil import parse_iso, to_iso

bp = Blueprint("api_device", __name__, url_prefix="/api")

OCCUPANCY_TO_STATE = {"person": "occupied", "item": "occupied", "empty": "empty"}
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
        seats = {r["no"]: r for r in db.execute("SELECT no, state FROM seats WHERE active = 1")}
        for it in items:
            if not isinstance(it, dict):
                raise ApiError(400, "BAD_REQUEST", "'seats' 항목은 객체여야 합니다.")
            seat_no = int_field(it, "seat_no")
            occ = it.get("occupancy")
            if occ not in OCCUPANCY_TO_STATE:
                raise ApiError(400, "BAD_REQUEST", f"좌석 {seat_no}: occupancy는 person|item|empty 중 하나여야 합니다.")
            seat = seats.get(seat_no)
            if seat is None or seat["state"] == "unavailable":
                ignored.append(seat_no)
                continue
            state = OCCUPANCY_TO_STATE[occ]
            if seat["state"] == state:
                accepted += 1  # 같은 상태면 시작 시각 유지
                continue
            since = now
            if it.get("since"):
                try:
                    since = parse_iso(it["since"]) + offset
                except (ValueError, TypeError):
                    raise ApiError(400, "BAD_REQUEST", f"좌석 {seat_no}: 'since' 시각 형식이 올바르지 않습니다.")
            db.execute("UPDATE seats SET state=?, state_since=?, state_source='camera', state_note=NULL WHERE no=?",
                       (state, min(since, now), seat_no))
            accepted += 1
        refresh(db, now)

    return jsonify({"ok": True, "accepted": accepted, "ignored": ignored, "server_time": to_iso(now)})
