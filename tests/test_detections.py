from conftest import admin_seat, send, set_state


def _state(conn, no):
    return conn.execute("SELECT state, state_since, state_source FROM seats WHERE no=?", (no,)).fetchone()


def test_missing_or_wrong_key_403(device, clock):
    assert send(device, clock, {1: ("person", clock())}, key=None).status_code == 403
    r = send(device, clock, {1: ("person", clock())}, key="nope")
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "BAD_DEVICE_KEY"


def test_unknown_seat_ignored(device, clock):
    body = send(device, clock, {1: ("person", clock()), 99: ("item", clock())}).get_json()
    assert body["ok"] and body["accepted"] == 1 and body["ignored"] == [99]
    assert body["server_time"].endswith("+09:00")


def test_bad_occupancy_400(device, clock):
    assert send(device, clock, {1: ("sleeping", clock())}).status_code == 400


def test_maps_to_three_states(device, clock, conn):
    send(device, clock, {1: ("person", clock() - 10), 2: ("item", clock() - 20), 3: ("empty", clock())})
    assert _state(conn, 1)["state"] == "occupied" and _state(conn, 1)["state_since"] == clock() - 10
    assert _state(conn, 2)["state"] == "item" and _state(conn, 2)["state_source"] == "camera"
    assert _state(conn, 3)["state"] == "empty"


def test_same_state_keeps_since(device, clock, conn):
    send(device, clock, {1: ("person", clock() - 10)})
    clock.advance(5)
    send(device, clock, {1: ("person", clock())})  # 같은 상태면 시작 시각 유지
    assert _state(conn, 1)["state_since"] == clock() - 15


def test_unavailable_not_overwritten(device, clock, conn):
    # seats.json 초기 배분: 5번(B-1)은 사용불가
    r = send(device, clock, {5: ("person", clock())}).get_json()
    assert r["ignored"] == [5] and _state(conn, 5)["state"] == "unavailable"


def test_clock_correction(device, clock, conn):
    pi_now = clock() - 100  # Pi 시계가 100초 느림
    send(device, clock, {1: ("person", pi_now - 20)}, ts=pi_now)
    assert _state(conn, 1)["state_since"] == clock() - 20


def test_camera_feeds_reconciliation(device, clock, admin):
    send(device, clock, {1: ("person", clock())})
    s = admin_seat(admin, 1)
    assert s["seat_state"] == "in_use" and s["situation"] == "unauthorized"
