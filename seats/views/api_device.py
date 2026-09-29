"""디바이스(Pi) → 웹: POST /api/detections.

카메라 연동 전에는 관리자가 좌석 상태를 임시로 부여한다. 카메라가 붙으면 이 API가 현장 상태를 갱신한다
(person → 사람 있음, item → 짐만 있음, empty → 비어 있음). 관리자가 '사용불가'로 지정한 좌석은 덮어쓰지 않는다.
"""
import hmac

from django.conf import settings
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .. import clock
from ..http import ApiError, int_field, jres, json_body
from ..models import Seat
from ..services import refresh
from ..timeutil import parse_iso, to_iso

OCCUPANCY_TO_STATE = {"person": "occupied", "item": "item", "empty": "empty"}
CLOCK_SKEW_TOLERANCE_SEC = 5  # 서버-Pi 시계 차이가 이 값(초)을 넘으면 since를 보정한다


@csrf_exempt
@require_POST
def detections(request):
    key = request.headers.get("X-Device-Key", "")
    if not key or not hmac.compare_digest(key.encode(), settings.SEATSYNC["DEVICE_KEY"].encode()):
        raise ApiError(403, "BAD_DEVICE_KEY", "디바이스 키가 올바르지 않습니다.")
    now = clock.now()
    body = json_body(request)
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

    accepted, ignored = 0, []
    with transaction.atomic():
        seats = {st.no: st for st in Seat.objects.filter(active=True)}
        for it in items:
            if not isinstance(it, dict):
                raise ApiError(400, "BAD_REQUEST", "'seats' 항목은 객체여야 합니다.")
            seat_no = int_field(it, "seat_no")
            occ = it.get("occupancy")
            if occ not in OCCUPANCY_TO_STATE:
                raise ApiError(400, "BAD_REQUEST", f"좌석 {seat_no}: occupancy는 person|item|empty 중 하나여야 합니다.")
            seat = seats.get(seat_no)
            if seat is None or seat.state == "unavailable":
                ignored.append(seat_no)
                continue
            state = OCCUPANCY_TO_STATE[occ]
            accepted += 1
            if seat.state == state:
                continue  # 같은 상태면 시작 시각과 관리자 지정 의도(mark)를 유지
            since = now
            if it.get("since"):
                try:
                    since = parse_iso(it["since"]) + offset
                except (ValueError, TypeError):
                    raise ApiError(400, "BAD_REQUEST", f"좌석 {seat_no}: 'since' 시각 형식이 올바르지 않습니다.")
            seat.state, seat.state_since, seat.state_source = state, min(since, now), "camera"
            seat.mark = seat.reason = seat.note = None
            seat.save(update_fields=["state", "state_since", "state_source", "mark", "reason", "note"])
        refresh(now)
    return jres({"ok": True, "accepted": accepted, "ignored": ignored, "server_time": to_iso(now)})
