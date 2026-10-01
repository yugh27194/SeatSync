import json
from pathlib import Path

import pytest
from django.test import Client

from seats import auth as authmod
from seats import clock as clockmod
from seats.models import Seat, User
from seats.seed import seed

T0 = 1_790_000_000  # 2026-09-21 경
DEVICE_KEY = "test-key"
SEATS20 = Path(__file__).parent / "seats20.json"
ADMIN_CODE = "test-code"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


class ApiClient(Client):
    def jpost(self, url, body=None):
        return self.post(url, data=json.dumps({} if body is None else body), content_type="application/json")

    def jput(self, url, body=None):
        return self.put(url, data=json.dumps({} if body is None else body), content_type="application/json")

    def jget(self, url):
        return self.get(url).json()


@pytest.fixture
def clock():
    c = Clock(T0)
    clockmod.set_clock(c)
    yield c
    clockmod.set_clock(None)


@pytest.fixture(autouse=True)
def seeded(db, settings, clock, tmp_path):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # 테스트 속도용
    # 테스트는 20석 배치(tests/seats20.json)로 돌린다. 실제 배치(config/seats.json)는 test_layout8.py에서 따로 확인
    settings.SEATSYNC = {**settings.SEATSYNC, "DEVICE_KEY": DEVICE_KEY, "ADMIN_CODE": ADMIN_CODE, "SEATS_FILE": SEATS20,
                         "QR_DIR": tmp_path / "qr", "PUBLIC_URL": ""}
    authmod._fails.clear()
    seed(now=T0)


def login(client, student_no="20260001", password="1234"):
    r = client.post("/login", {"student_no": student_no, "password": password})
    assert r.status_code == 302, r.content
    return client


@pytest.fixture
def user():
    return login(ApiClient())


@pytest.fixture
def user2():
    return login(ApiClient(), "20260002")


@pytest.fixture
def user_a():
    return login(ApiClient(), "userA")


@pytest.fixture
def user_b():
    return login(ApiClient(), "userB")


@pytest.fixture
def admin():
    """관리자 계정은 없다: 일반 사용자(테스트5)로 로그인한 뒤 관리자 모드를 켠다."""
    c = login(ApiClient(), "20260005")
    r = c.jpost("/api/admin-mode/unlock", {"code": ADMIN_CODE})
    assert r.status_code == 200, r.json()
    return c


@pytest.fixture
def device():
    return ApiClient()


def send(device, clock, seats, ts=None, key=DEVICE_KEY):
    """seats: {seat_no: (occupancy, since_epoch 또는 None)}"""
    from seats.timeutil import to_iso
    body = {"camera_id": "cam1", "ts": to_iso(ts if ts is not None else clock()), "seats": []}
    for no, (occ, since) in seats.items():
        item = {"seat_no": no, "occupancy": occ}
        if since is not None:
            item["since"] = to_iso(since)
        body["seats"].append(item)
    headers = {"HTTP_X_DEVICE_KEY": key} if key else {}
    return device.post("/api/detections", data=json.dumps(body), content_type="application/json", **headers)


def set_state(admin, no, detail, note=None):
    body = {"detail": detail}
    if note:
        body["note"] = note
    r = admin.jpost(f"/api/admin/seats/{no}/state", body)
    assert r.status_code == 200, r.json()
    return r


def qr_token(no):
    return Seat.objects.get(no=no).qr_token


def user_id(student_no):
    return User.objects.get(student_no=student_no).id


def admin_seat(admin, no):
    return next(s for s in admin.jget("/api/admin/seats")["seats"] if s["no"] == no)


def err(r):
    return r.json()["error"]["code"]
