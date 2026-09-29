from conftest import admin_seat, send


def test_missing_key_403(device, clock):
    r = send(device, clock, {1: ("person", clock())}, key=None)
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "BAD_DEVICE_KEY"


def test_wrong_key_403(device, clock):
    r = send(device, clock, {1: ("person", clock())}, key="nope")
    assert r.status_code == 403


def test_unknown_seat_ignored(device, clock):
    r = send(device, clock, {1: ("person", clock()), 99: ("item", clock())})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["accepted"] == 1 and body["ignored"] == [99]
    assert body["server_time"].endswith("+09:00")


def test_bad_occupancy_400(device, clock):
    r = send(device, clock, {1: ("sleeping", clock())})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "BAD_REQUEST"


def test_upsert(device, clock, conn):
    send(device, clock, {1: ("person", clock() - 10)})
    clock.advance(5)
    send(device, clock, {1: ("item", clock() - 2)})
    rows = conn.execute("SELECT * FROM detections WHERE seat_no = 1").fetchall()
    assert len(rows) == 1
    assert rows[0]["occupancy"] == "item"
    assert rows[0]["since"] == clock() - 2
    assert rows[0]["updated_at"] == clock()


def test_clock_correction(device, clock, conn):
    # Pi 시계가 100초 느림 → since에 +100 보정
    pi_now = clock() - 100
    send(device, clock, {1: ("person", pi_now - 20)}, ts=pi_now)
    row = conn.execute("SELECT since FROM detections WHERE seat_no = 1").fetchone()
    assert row["since"] == clock() - 20


def test_small_skew_not_corrected(device, clock, conn):
    pi_now = clock() - 3
    send(device, clock, {1: ("person", pi_now - 20)}, ts=pi_now)
    row = conn.execute("SELECT since FROM detections WHERE seat_no = 1").fetchone()
    assert row["since"] == pi_now - 20


def test_since_not_in_future(device, clock, conn):
    send(device, clock, {1: ("person", clock() + 50)})
    row = conn.execute("SELECT since FROM detections WHERE seat_no = 1").fetchone()
    assert row["since"] == clock()


def test_admin_seats_reflect_detection(device, clock, admin):
    send(device, clock, {1: ("person", clock() - 600), 2: ("item", clock()), 3: ("empty", clock())})
    assert admin_seat(admin, 1)["state"] == "UNAUTHORIZED"
    assert admin_seat(admin, 2)["state"] == "ITEM_ONLY"
    assert admin_seat(admin, 3)["state"] == "AVAILABLE"
    assert admin_seat(admin, 4)["state"] == "OFFLINE"  # 감지 없음


def test_stale_becomes_offline(device, clock, admin):
    send(device, clock, {1: ("empty", clock())})
    clock.advance(31)
    assert admin_seat(admin, 1)["state"] == "OFFLINE"
