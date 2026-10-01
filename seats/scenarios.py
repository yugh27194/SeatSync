"""시연 시나리오: 실제 이용 중 겪을 수 있는 상황을 좌석에 바로 만들어 처리 흐름을 보여 준다(콘솔 전용).

    python manage.py scenario 1 --demo-settings

등장인물: 사용자A(userA) · 사용자B(userB) · 사용자C(userC) · 관리자(manager, 관리자 탭은 관리자 코드 필요)

  1   userA가 A-1을 예약(체크인 전)했는데 userB가 A-1에 마음대로 앉음
  1b  userA가 A-1에 체크인하고 잠깐 나간 사이 userB가 A-1에 앉음
  2a  userC가 B-2에 예약·QR 체크인을 하고 자리에 앉지 않음
  2b  userC가 B-2를 예약하고 QR 체크인 없이 앉음
  3   userA가 A-1을 예약하고 착각해 B-1에 앉음
  reset  모든 좌석을 빈자리로 (진행 중 예약 취소)

카메라가 연결되어 있으면 다음 감지 결과가 현장 상태를 덮어쓴다 — 시연은 카메라 없이(또는 카메라를 끄고) 한다.
"""
from django.db import transaction

from .models import Alert, Reservation, Seat, SeatState, Setting, User, WaitEntry
from .services import ACTIVE, get_settings, record_event, refresh, set_seat_state

# 시연용 판정 기준(설정 화면 [시연 모드]와 같은 값): 상황마다 1분 안팎으로 상태가 바뀐다.
DEMO_SETTINGS = {"checkin_limit_min": 2, "away_limit_min": 1, "hoarding_min": 1, "unauthorized_min": 1,
                 "unknown_min": 0.5, "auto_return_min": 2, "prewarn_min": 0.25, "waitlist_hold_min": 1}

SCENARIOS = {
    "1": "userA가 A-1을 예약(체크인 전)했는데 userB가 A-1에 마음대로 앉음",
    "1b": "userA가 A-1에 체크인하고 잠깐 나간 사이 userB가 A-1에 앉음",
    "2a": "userC가 B-2에 예약·QR 체크인을 하고 자리에 앉지 않음",
    "2b": "userC가 B-2를 예약하고 QR 체크인 없이 앉음",
    "3": "userA가 A-1을 예약하고 착각해 B-1에 앉음",
    "reset": "모든 좌석을 빈자리로",
}


def apply_demo_settings():
    for k, v in DEMO_SETTINGS.items():
        Setting.objects.update_or_create(key=k, defaults={"value": str(v)})


def _reset(now):
    Reservation.objects.filter(status__in=ACTIVE).update(status="cancelled", ended_at=now)
    WaitEntry.objects.filter(status__in=("waiting", "offered")).update(status="cancelled", ended_at=now)
    Alert.objects.filter(resolved_at__isnull=True).update(resolved_at=now, resolution="reset")
    SeatState.objects.all().delete()
    Seat.objects.filter(active=True).update(state="empty", mark=None, reason=None, note=None, state_since=now,
                                            state_source="manual", cam_state=None, cam_unknown_since=None)


def _reserve(student_no, label, now, checked_in=False):
    s = get_settings()
    user, seat = User.objects.get(student_no=student_no), Seat.objects.get(label=label, active=True)
    r = Reservation.objects.create(user=user, seat=seat, status="in_use" if checked_in else "reserved", start_at=now,
                                   end_at=now + s.sec("default_use_min"), checked_in_at=now if checked_in else None)
    record_event(r, "reserve", now, memo="시나리오")
    if checked_in:
        record_event(r, "checkin", now, memo="좌석 QR")
    return r


def _sit(label, now, state="occupied"):
    set_seat_state(Seat.objects.get(label=label, active=True), state, "manual", now)


def setup_scenario(name, now):
    """상황을 만들고 안내 문구 목록을 돌려준다."""
    if name not in SCENARIOS:
        raise ValueError(f"알 수 없는 시나리오: {name} ({', '.join(SCENARIOS)})")
    with transaction.atomic():
        _reset(now)
        if name == "1":
            _reserve("userA", "A-1", now)
            _sit("A-1", now)  # userB가 앉음 (카메라: 사람 있음)
        elif name == "1b":
            _reserve("userA", "A-1", now - 600, checked_in=True)
            _sit("A-1", now)  # userA가 나간 사이 userB가 앉음 — 카메라로는 같은 '사람 있음'
        elif name == "2a":
            _reserve("userC", "B-2", now, checked_in=True)
            _sit("B-2", now, "empty")  # 체크인했지만 자리에 없음
        elif name == "2b":
            _reserve("userC", "B-2", now)
            _sit("B-2", now)  # QR 체크인 없이 착석
        elif name == "3":
            _reserve("userA", "A-1", now)
            _sit("B-1", now)  # userA가 B-1에 착각해 앉음
        refresh(now)
    return [f"[{name}] {SCENARIOS[name]}"]
