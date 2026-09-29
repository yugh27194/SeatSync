"""디바이스(라즈베리파이) → 웹 연동.

POST /api/detections  — 감지 결과 수신. 두 형식을 받는다.
  1) 감지 프로토타입 스냅샷(schema_version 1): seat_monitor가 쓰는 output/status.json을 그대로 보낸다.
     (tools/pi_bridge.py가 camera_id와 sent_at(전송 시각, 시계 보정용)을 붙여 전송)
       {"schema_version": 1, "camera_id": "cam1", "sent_at": "...", "observed_at": "...+00:00", "valid_until": "...",
        "health": "ok", "meaning": "person_presence_only",
        "seats": [{"seat_id": "A01", "state": "OCCUPIED|EMPTY|UNKNOWN", "current_person_confidence": 0.91,
                   "state_duration_seconds": 12.3, ...}]}
  2) occupancy 형식(짐 구분이 되는 감지기용): {"camera_id", "ts", "seats": [{"seat_no", "occupancy": person|item|empty, "since"}]}

GET /api/device/config?camera_id=cam1 — 카메라 좌석 ID(A01…) ↔ 웹 좌석 대응표. 브리지가 시작할 때 확인한다.

서버는 영상·이미지를 받지 않는다. 좌석별 상태 문자열과 점수만 받는다.
"""
import hmac

from django.conf import settings
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .. import clock
from ..http import ApiError, int_field, jres, json_body
from ..models import Camera, Seat
from ..services import refresh
from ..timeutil import parse_iso, to_iso

OCCUPANCY_TO_STATE = {"person": "occupied", "item": "item", "empty": "empty"}
CAM_STATES = ("OCCUPIED", "EMPTY", "UNKNOWN")
CLOCK_SKEW_TOLERANCE_SEC = 5   # 서버-Pi 시계 차이가 이 값(초)을 넘으면 시각을 보정한다
DEFAULT_TTL_SEC = 10           # valid_until이 없을 때 유효 시간
MAX_TTL_SEC = 120              # 비정상적으로 긴 valid_until은 잘라낸다
POST_INTERVAL_SEC = 2          # 브리지 권장 전송 주기


def _check_key(request):
    key = request.headers.get("X-Device-Key", "")
    if not key or not hmac.compare_digest(key.encode(), settings.SEATSYNC["DEVICE_KEY"].encode()):
        raise ApiError(403, "BAD_DEVICE_KEY", "디바이스 키가 올바르지 않습니다.")


def _parse_time(value, field):
    try:
        return parse_iso(value)
    except (ValueError, TypeError):
        raise ApiError(400, "BAD_REQUEST", f"'{field}' 시각 형식이 올바르지 않습니다.")


@csrf_exempt
@require_POST
def detections(request):
    _check_key(request)
    body = json_body(request)
    if not isinstance(body.get("seats"), list):
        raise ApiError(400, "BAD_REQUEST", "'seats' 필드는 배열이어야 합니다.")
    now = clock.now()
    with transaction.atomic():
        if "schema_version" in body:
            result = _ingest_snapshot(request, body, now)
        else:
            result = _ingest_occupancy(body, now)
        refresh(now)
    return jres({"ok": True, **result, "server_time": to_iso(now)})


# ---------------------------------------------------------------- 1) 감지 프로토타입 스냅샷

def _ingest_snapshot(request, body, now):
    if body.get("schema_version") != 1:
        raise ApiError(400, "UNSUPPORTED_SCHEMA", f"지원하지 않는 schema_version입니다: {body.get('schema_version')}")
    camera_id = (body.get("camera_id") or request.headers.get("X-Camera-Id") or "").strip()
    if not camera_id:
        raise ApiError(400, "BAD_REQUEST", "camera_id(본문) 또는 X-Camera-Id(헤더)가 필요합니다.")
    health = str(body.get("health") or "")
    meaning = str(body.get("meaning") or "")
    observed = _parse_time(body.get("observed_at"), "observed_at") if body.get("observed_at") else now
    # 시계 보정은 브리지가 붙인 sent_at(Pi 시계 기준 전송 시각)으로만 한다.
    # observed_at으로 보정하면, 멈춘 감지기의 오래된 스냅샷을 '시계가 느린 것'으로 오해해 새것처럼 만들어 버린다.
    offset = 0
    if body.get("sent_at"):
        offset = now - _parse_time(body["sent_at"], "sent_at")
        if abs(offset) <= CLOCK_SKEW_TOLERANCE_SEC:
            offset = 0
    observed = min(observed + offset, now)
    if body.get("valid_until"):
        valid_until = _parse_time(body["valid_until"], "valid_until") + offset
    else:
        valid_until = observed + DEFAULT_TTL_SEC
    valid_until = min(valid_until, now + MAX_TTL_SEC)
    fresh = health == "ok" and valid_until >= now

    Camera.objects.update_or_create(camera_id=camera_id, defaults={
        "health": health, "meaning": meaning, "schema_version": 1, "observed_at": observed,
        "valid_until": valid_until, "last_seen_at": now, "clock_offset": offset,
        "seats_reported": len(body["seats"])})

    mapped = {s.camera_seat: s for s in Seat.objects.filter(active=True, camera_id=camera_id).exclude(camera_seat="")}
    # 사람만 감지하는 카메라(person_presence_only)는 'EMPTY'가 '짐만 있음'일 수도 있다.
    person_only = meaning in ("", "person_presence_only")
    accepted, ignored, unknown = 0, [], []
    seen = set()
    for it in body["seats"]:
        if not isinstance(it, dict):
            raise ApiError(400, "BAD_REQUEST", "'seats' 항목은 객체여야 합니다.")
        sid = str(it.get("seat_id") or "")
        state = it.get("state")
        if state not in CAM_STATES:
            raise ApiError(400, "BAD_REQUEST", f"좌석 {sid}: state는 OCCUPIED|EMPTY|UNKNOWN 중 하나여야 합니다.")
        seat = mapped.get(sid)
        if seat is None:
            ignored.append(sid)
            continue
        seen.add(sid)
        accepted += 1
        conf = it.get("current_person_confidence")
        seat.cam_confidence = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
        seat.cam_seen_at, seat.cam_valid_until = now, valid_until
        if not fresh:
            state = "UNKNOWN"  # health가 ok가 아니거나 이미 유효 시간이 지난 스냅샷
        if state == "UNKNOWN":
            unknown.append(sid)
            if seat.cam_state != "UNKNOWN":
                seat.cam_unknown_since = now
            seat.cam_state = state
            seat.save(update_fields=["cam_state", "cam_confidence", "cam_seen_at", "cam_valid_until", "cam_unknown_since"])
            continue  # 확인 불가: 마지막으로 확인된 좌석 상태를 유지한다
        seat.cam_state, seat.cam_unknown_since = state, None
        fields = ["cam_state", "cam_confidence", "cam_seen_at", "cam_valid_until", "cam_unknown_since"]
        if seat.state != "unavailable":  # 관리자가 사용불가로 지정한 좌석은 덮어쓰지 않는다
            target = "occupied" if state == "OCCUPIED" else "empty"
            if target == "empty" and person_only and seat.state == "item":
                target = "item"  # 사람만 보는 카메라의 EMPTY는 짐이 남아 있을 수 있으므로 '짐만 있음'을 유지
            kept_item = target == "item"
            if target != seat.state:
                dur = it.get("state_duration_seconds")
                dur = dur if isinstance(dur, (int, float)) and not isinstance(dur, bool) and dur >= 0 else 0
                seat.state, seat.state_since = target, max(0, min(now, round(observed - dur)))
                seat.mark = seat.reason = seat.note = None
                seat.state_source = "camera"
                fields += ["state", "state_since", "mark", "reason", "note", "state_source"]
            elif not kept_item and seat.state_source != "camera":
                seat.state_source = "camera"  # 같은 상태를 카메라가 확인 → 이후 감지 끊김 판단 대상 (관리자 지정 의도는 유지)
                fields.append("state_source")
        seat.save(update_fields=list(dict.fromkeys(fields)))
    # 대응표에는 있는데 스냅샷에 빠진 좌석: 카메라 설정(calibrate)과 웹 설정이 어긋남 → 확인 불가로 표시
    missing = sorted(set(mapped) - seen)
    for sid in missing:
        seat = mapped[sid]
        if seat.cam_state != "UNKNOWN":
            seat.cam_state, seat.cam_unknown_since = "UNKNOWN", now
            seat.save(update_fields=["cam_state", "cam_unknown_since"])
    return {"format": "snapshot_v1", "camera_id": camera_id, "health": health, "fresh": fresh,
            "accepted": accepted, "ignored": ignored, "unknown": unknown, "missing": missing,
            "clock_offset": offset}


# ---------------------------------------------------------------- 2) occupancy 형식 (짐 구분 감지기)

def _ingest_occupancy(body, now):
    offset = 0
    if body.get("ts"):
        offset = now - _parse_time(body["ts"], "ts")
        if abs(offset) <= CLOCK_SKEW_TOLERANCE_SEC:
            offset = 0
    seats = {st.no: st for st in Seat.objects.filter(active=True)}
    accepted, ignored = 0, []
    for it in body["seats"]:
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
        seat.cam_state = "EMPTY" if occ == "empty" else "OCCUPIED"
        seat.cam_seen_at, seat.cam_valid_until, seat.cam_unknown_since = now, now + DEFAULT_TTL_SEC * 3, None
        fields = ["cam_state", "cam_seen_at", "cam_valid_until", "cam_unknown_since"]
        if seat.state != state:  # 같은 상태면 시작 시각과 관리자 지정 의도(mark)를 유지
            since = now
            if it.get("since"):
                since = _parse_time(it["since"], "since") + offset
            seat.state, seat.state_since, seat.state_source = state, min(since, now), "camera"
            seat.mark = seat.reason = seat.note = None
            fields += ["state", "state_since", "state_source", "mark", "reason", "note"]
        seat.save(update_fields=fields)
    return {"format": "occupancy", "accepted": accepted, "ignored": ignored}


# ---------------------------------------------------------------- 카메라 좌석 대응표

@csrf_exempt
@require_GET
def device_config(request):
    _check_key(request)
    camera_id = (request.GET.get("camera_id") or request.headers.get("X-Camera-Id") or "").strip()
    qs = Seat.objects.filter(active=True).exclude(camera_seat="").order_by("camera_id", "camera_seat")
    if camera_id:
        qs = qs.filter(camera_id=camera_id)
    return jres({
        "camera_id": camera_id or None,
        "seats": [{"camera_id": s.camera_id, "camera_seat": s.camera_seat, "seat_no": s.no, "label": s.label,
                   "zone": s.zone} for s in qs],
        "post_interval_sec": POST_INTERVAL_SEC,
        "server_time": to_iso(clock.now()),
    })
