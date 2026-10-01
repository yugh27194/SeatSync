from conftest import admin_seat, send

from seats.models import Seat


def _seat(no):
    return Seat.objects.get(no=no)


def test_missing_or_wrong_key_403(device, clock):
    assert send(device, clock, {1: ("person", clock())}, key=None).status_code == 403
    r = send(device, clock, {1: ("person", clock())}, key="nope")
    assert r.status_code == 403 and r.json()["error"]["code"] == "BAD_DEVICE_KEY"


def test_unknown_seat_ignored(device, clock):
    body = send(device, clock, {1: ("person", clock()), 99: ("item", clock())}).json()
    assert body["ok"] and body["accepted"] == 1 and body["ignored"] == [99]
    assert body["server_time"].endswith("+09:00")


def test_bad_occupancy_400(device, clock):
    assert send(device, clock, {1: ("sleeping", clock())}).status_code == 400


def test_maps_states(device, clock):
    send(device, clock, {1: ("person", clock() - 10), 2: ("item", clock() - 20), 3: ("empty", clock())})
    assert _seat(1).state == "occupied" and _seat(1).state_since == clock() - 10
    assert _seat(2).state == "item" and _seat(2).state_source == "camera"
    assert _seat(3).state == "empty" and _seat(3).mark is None


def test_same_state_keeps_since_and_mark(device, clock, admin):
    from conftest import set_state
    set_state(admin, 1, "using")  # 관리자가 확인한 이용
    since = _seat(1).state_since
    clock.advance(5)
    send(device, clock, {1: ("person", clock())})
    assert _seat(1).state_since == since and _seat(1).mark == "ok"
    send(device, clock, {1: ("empty", clock())})  # 상태가 바뀌면 관리자 지정 의도는 사라짐
    assert _seat(1).mark is None


def test_unavailable_not_overwritten(device, clock):
    r = send(device, clock, {20: ("person", clock())}).json()  # E-3: 초기 배분 '고장'
    assert r["ignored"] == [20] and _seat(20).state == "unavailable"


def test_clock_correction(device, clock):
    pi_now = clock() - 100  # Pi 시계가 100초 느림
    send(device, clock, {1: ("person", pi_now - 20)}, ts=pi_now)
    assert _seat(1).state_since == clock() - 20


def test_camera_person_without_reservation_is_unauthorized(device, clock, admin):
    t0 = clock()
    send(device, clock, {1: ("person", t0)})
    s = admin_seat(admin, 1)  # QR 체크인 없이 착석 → 기준 시간(10분) 전에는 착석 감지(확인 필요 아님)
    assert s["seat_state"] == "in_use" and s["detail"] == "detected" and not s["needs_action"] and not s["check"]
    assert s["deadline_sec"] == 600 and s["next_label"] == "무단 점유"
    clock.advance(600)
    send(device, clock, {1: ("person", t0)})
    s = admin_seat(admin, 1)
    assert s["seat_state"] == "in_use" and s["detail"] == "unauthorized" and s["needs_action"]
