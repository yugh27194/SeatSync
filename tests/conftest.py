import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as dbmod  # noqa: E402
from app import create_app  # noqa: E402
from timeutil import to_iso  # noqa: E402

T0 = 1_790_000_000  # 2026-09-21 경
DEVICE_KEY = "test-key"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


@pytest.fixture
def clock():
    return Clock(T0)


@pytest.fixture
def app(tmp_path, clock):
    path = str(tmp_path / "test.db")
    app = create_app({"TESTING": True, "DATABASE": path, "CLOCK": clock, "DEVICE_KEY": DEVICE_KEY,
                      "SECRET_KEY": "test"})
    conn = dbmod.connect(path)
    dbmod.init_db(conn)
    dbmod.seed(conn, app.config["SEATS_FILE"], now=T0, pw_method="pbkdf2:sha256:1000")  # 테스트 속도용
    conn.close()
    return app


@pytest.fixture
def conn(app):
    c = dbmod.connect(app.config["DATABASE"])
    yield c
    c.close()


def login(client, student_no="20260001", password="1234"):
    r = client.post("/login", data={"student_no": student_no, "password": password})
    assert r.status_code == 302, r.data
    return client


@pytest.fixture
def user(app):
    return login(app.test_client())


@pytest.fixture
def user2(app):
    return login(app.test_client(), "20260002")


@pytest.fixture
def user_a(app):
    return login(app.test_client(), "userA")


@pytest.fixture
def user_b(app):
    return login(app.test_client(), "userB")


@pytest.fixture
def admin(app):
    return login(app.test_client(), "admin", "admin1234")


@pytest.fixture
def device(app):
    return app.test_client()


def send(device, clock, seats, ts=None, key=DEVICE_KEY):
    """seats: {seat_no: (occupancy, since_epoch)} 또는 (occupancy, None)"""
    body = {"camera_id": "cam1", "ts": to_iso(ts if ts is not None else clock()), "seats": []}
    for no, (occ, since) in seats.items():
        item = {"seat_no": no, "occupancy": occ}
        if since is not None:
            item["since"] = to_iso(since)
        body["seats"].append(item)
    headers = {"X-Device-Key": key} if key else {}
    return device.post("/api/detections", json=body, headers=headers)


def qr_token(conn, seat_no):
    return conn.execute("SELECT qr_token FROM seats WHERE no=?", (seat_no,)).fetchone()["qr_token"]


def set_state(admin, no, state, note=None):
    body = {"state": state}
    if note:
        body["note"] = note
    r = admin.post(f"/api/admin/seats/{no}/state", json=body)
    assert r.status_code == 200, r.get_json()
    return r


def user_id(conn, student_no):
    return conn.execute("SELECT id FROM users WHERE student_no=?", (student_no,)).fetchone()["id"]


def admin_seat(admin, no):
    data = admin.get("/api/admin/seats").get_json()
    return next(s for s in data["seats"] if s["no"] == no)
