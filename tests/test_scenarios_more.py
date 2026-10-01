"""추가 시나리오 4~16 (docs/SCENARIOS.md). 실제 8석 배치 + 시연용 판정 기준(장기 이석·무단 점유 1분)."""
import pytest
from conftest import ADMIN_CODE, T0, ApiClient, admin_seat, err, login, qr_token
from test_camera import post, snapshot

from seats.models import Alert, Notification, Reservation, Seat, User, WaitEntry
from seats.scenarios import apply_demo_settings, setup_scenario
from seats.seed import seed


@pytest.fixture(autouse=True)
def room(settings, clock):
    settings.SEATSYNC = {**settings.SEATSYNC, "SEATS_FILE": settings.BASE_DIR / "config" / "seats.json"}
    seed(now=T0)
    apply_demo_settings()
    setup_scenario("reset", clock())


def no(label):
    return Seat.objects.get(label=label, active=True).no


def notes(sno, kind=None):
    qs = Notification.objects.filter(user__student_no=sno)
    return [n.title for n in (qs.filter(kind=kind) if kind else qs).order_by("id")]


@pytest.fixture
def manager():
    c = login(ApiClient(), "manager")
    assert c.jpost("/api/admin-mode/unlock", {"code": ADMIN_CODE}).status_code == 200
    return c


@pytest.fixture
def ua():
    return login(ApiClient(), "userA")


@pytest.fixture
def ub():
    return login(ApiClient(), "userB")


@pytest.fixture
def uc():
    return login(ApiClient(), "userC")


def reserve(c, label, qr=False):
    body = {"seat_no": no(label)}
    if qr:
        body["qr_token"] = qr_token(no(label))
    r = c.jpost("/api/reservations", body)
    assert r.status_code == 201, r.json()
    return r.json()["id"]


def test_04_no_show(clock, manager, ua):
    """예약만 하고 오지 않음 → 마감 15초 전 알림 → 2분: 미입실(이석·장기) → 4분: 예약 자동 취소."""
    rid = reserve(ua, "A-1")
    assert admin_seat(manager, no("A-1"))["detail"] == "waiting"
    clock.advance(105)
    ua.get("/api/seats")
    assert any("체크인 마감" in t for t in notes("userA", "prewarn"))
    clock.advance(15)
    s = admin_seat(manager, no("A-1"))
    assert (s["detail"], s["category_label"]) == ("no_show", "이석") and s["needs_action"]
    clock.advance(120)
    admin_seat(manager, no("A-1"))
    assert Reservation.objects.get(id=rid).status == "no_show"
    assert any("자동으로 취소" in t for t in notes("userA"))


def test_05_extend(clock, ua):
    """종료 30분 전부터 연장 가능, 1회 +60분, 최대 2회."""
    rid = reserve(ua, "A-1", qr=True)
    assert err(ua.jpost(f"/api/reservations/{rid}/extend")) == "EXTEND_NOT_ALLOWED"  # 아직 이르다
    clock.advance(91 * 60)
    end0 = Reservation.objects.get(id=rid).end_at
    assert ua.jpost(f"/api/reservations/{rid}/extend").status_code == 200
    assert Reservation.objects.get(id=rid).end_at == end0 + 3600
    clock.advance(60 * 60)
    assert ua.jpost(f"/api/reservations/{rid}/extend").status_code == 200
    clock.advance(60 * 60)
    r = ua.jpost(f"/api/reservations/{rid}/extend")
    assert err(r) == "EXTEND_NOT_ALLOWED" and "최대 2회" in r.json()["error"]["message"]


def test_06_time_over(clock, manager, ua):
    """이용 시간이 끝남 → 예약 자동 종료(시간 만료). 계속 앉아 있으면 1분 뒤 무단 점유."""
    rid = reserve(ua, "A-1", qr=True)
    clock.advance(120 * 60)
    s = admin_seat(manager, no("A-1"))
    assert Reservation.objects.get(id=rid).status == "expired" and s["detail"] == "detected"
    clock.advance(60)
    assert admin_seat(manager, no("A-1"))["detail"] == "unauthorized"


def test_07_bag_left_for_lunch(clock, manager, ub, device):
    """짐만 두고 식사 → 카메라는 사람이 없음(일시 이석) → 관리자가 순찰 중 [짐만 있음] 지정 → 1분 뒤 사석화 → 자동 반납."""
    rid = reserve(ub, "B-2", qr=True)
    clock.advance(5)
    post(device, snapshot(clock, {"A06": ("EMPTY", 0, 0)}))
    assert admin_seat(manager, no("B-2"))["detail"] == "away_short"
    assert manager.jpost(f"/api/admin/seats/{no('B-2')}/state", {"detail": "item"}).status_code == 200
    s = admin_seat(manager, no("B-2"))
    assert (s["detail"], s["category_label"]) == ("item", "이석")
    clock.advance(60)
    s = admin_seat(manager, no("B-2"))
    assert (s["detail"], s["category_label"]) == ("hoarding", "이석") and s["needs_action"]
    clock.advance(120)
    admin_seat(manager, no("B-2"))
    assert Reservation.objects.get(id=rid).status == "force_returned"


def test_08_waitlist(clock, manager, ua, ub, uc):
    """빈자리가 없을 때 빈자리 알림 → 자리가 나면 순서대로 1분 우선 예약 → 안 쓰면 다음 대기자에게."""
    users = ["20260001", "20260002", "20260003", "20260004", "20260005", "userA", "userB", "manager"]
    seats = ["A-1", "A-2", "A-3", "A-4", "B-1", "B-2", "B-3", "B-4"]
    for sno, lbl in zip(users, seats):  # 8석 모두 예약
        reserve(login(ApiClient(), sno), lbl)
    assert uc.jpost("/api/waitlist", {}).status_code == 201
    second = User.objects.create_user("20269999", "대기자2", "1234")
    w2 = login(ApiClient(), "20269999")
    assert w2.jpost("/api/waitlist", {}).status_code == 201
    a1 = Reservation.objects.get(seat_id=no("A-1"), status="reserved")
    login(ApiClient(), "20260001").jpost(f"/api/reservations/{a1.id}/return")
    d = uc.jget("/api/seats")
    assert d["waitlist"]["status"] == "offered" and d["waitlist"]["offer"]["seat_label"] == "A-1"
    assert err(w2.jpost("/api/reservations", {"seat_no": no("A-1")})) == "SEAT_HELD"
    clock.advance(61)
    w2.get("/api/seats")
    assert WaitEntry.objects.get(user=second).status == "offered"  # 1분이 지나 다음 대기자에게
    assert w2.jpost("/api/reservations", {"seat_no": no("A-1")}).status_code == 201


def test_09_same_seat_at_once(ua, ub):
    """두 사람이 같은 좌석을 동시에 예약 → 한 명만 성공."""
    reserve(ua, "A-2")
    r = ub.jpost("/api/reservations", {"seat_no": no("A-2")})
    assert err(r) == "SEAT_TAKEN"
    assert err(ua.jpost("/api/reservations", {"seat_no": no("A-3")})) == "ALREADY_HAS_RESERVATION"  # 1인 1석


def test_10_qr_photo_remote_checkin(clock, manager, ua, device):
    """친구가 QR 사진으로 대신 체크인(자리엔 아무도 없음) → 카메라가 빈 좌석으로 봄 → 일시 이석 → 1분 장기 이석 → 자동 반납."""
    rid = reserve(ua, "A-3", qr=True)  # QR 사진으로 체크인 성공(막을 수 없음)
    clock.advance(5)
    post(device, snapshot(clock, {"A03": ("EMPTY", 0, 5)}))
    assert admin_seat(manager, no("A-3"))["category_label"] == "이석"
    clock.advance(60)
    post(device, snapshot(clock, {"A03": ("EMPTY", 0, 65)}))
    s = admin_seat(manager, no("A-3"))
    assert s["detail"] == "away" and s["needs_action"]
    clock.advance(120)
    post(device, snapshot(clock, {"A03": ("EMPTY", 0, 185)}))
    assert Reservation.objects.get(id=rid).status == "force_returned"


def test_11_broken_seat(clock, manager, uc):
    """예약석 콘센트 고장 → 관리자 호출 → 관리자가 사용불가 지정 → 예약 좌석 사용불가 → 다른 좌석으로 이동."""
    rid = reserve(uc, "B-3", qr=True)
    assert uc.jpost("/api/calls", {"seat_no": no("B-3"), "memo": "콘센트가 안 돼요"}).status_code == 201
    assert manager.jpost(f"/api/admin/seats/{no('B-3')}/state", {"detail": "broken", "note": "콘센트"}).status_code == 200
    s = admin_seat(manager, no("B-3"))
    assert s["detail"] == "seat_unavailable" and s["needs_action"]
    assert any("사용할 수 없게" in t for t in notes("userC"))
    assert manager.jpost(f"/api/admin/reservations/{rid}/move", {"seat_no": no("B-4")}).status_code == 200
    assert admin_seat(manager, no("B-4"))["detail"] == "using"
    assert uc.jget(f"/api/seats/{no('B-3')}")["page_mode"] == "unavailable"


def test_12_camera_blocked(clock, manager, ub, device):
    """외투·가방에 가려 카메라가 인식 못 함(UNKNOWN) → 30초 뒤 판단 불가 → 관리자 현장 확인 → 정상 이용."""
    reserve(ub, "B-1", qr=True)
    clock.advance(5)
    post(device, snapshot(clock, {"A05": ("OCCUPIED", 0.9, 5)}))
    post(device, snapshot(clock, {"A05": ("UNKNOWN", 0, 0)}))
    assert admin_seat(manager, no("B-1"))["detail"] == "using"
    clock.advance(30)
    post(device, snapshot(clock, {"A05": ("UNKNOWN", 0, 0)}))
    s = admin_seat(manager, no("B-1"))
    assert (s["detail"], s["category"]) == ("unknown", "unknown") and s["needs_action"]
    assert manager.jpost(f"/api/admin/seats/{no('B-1')}/state", {"detail": "using"}).status_code == 200
    s = admin_seat(manager, no("B-1"))
    assert s["detail"] == "using" and not s["needs_action"]


def test_13_camera_off(clock, manager, ua, device):
    """카메라(라즈베리파이)가 꺼짐 → 30초 뒤 카메라 좌석 판단 불가 → 장기 이석·자동 반납으로 넘기지 않음."""
    rid = reserve(ua, "A-4", qr=True)
    clock.advance(5)
    post(device, snapshot(clock, {"A04": ("EMPTY", 0, 0)}))  # 잠깐 자리 비운 사이 카메라가 꺼짐
    clock.advance(40)
    s = admin_seat(manager, no("A-4"))
    assert s["detail"] == "unknown" and s["stale"]
    clock.advance(10 * 60)
    admin_seat(manager, no("A-4"))
    assert Reservation.objects.get(id=rid).status == "in_use"  # 끊긴 동안은 자동 반납하지 않는다


def test_14_warnings_then_suspension(clock, manager, ub):
    """반복 위반 → 경고 3회 → 이용 정지 권장 → 정지 → 예약·빈자리 알림 불가."""
    uid = User.objects.get(student_no="userB").id
    for i in range(3):
        r = manager.jpost(f"/api/admin/users/{uid}/warn", {"reason": "장기 이석 반복"}).json()
    assert r["warnings"] == 3 and r["suspend_suggested"]
    assert manager.jpost(f"/api/admin/users/{uid}/suspend", {"days": 3, "reason": "경고 3회"}).status_code == 200
    assert err(ub.jpost("/api/reservations", {"seat_no": no("A-1")})) == "SUSPENDED"
    assert err(ub.jpost("/api/waitlist", {})) == "SUSPENDED"
    assert any("경고" in t for t in notes("userB"))


def test_15_first_visit_by_qr():
    """처음 온 사람이 좌석 QR을 찍음 → 로그인 화면 → 회원가입 → 그 좌석으로 돌아와 바로 예약·체크인."""
    path = f"/seat/{no('B-4')}?t={qr_token(no('B-4'))}"
    c = ApiClient()
    assert c.get(path)["Location"].startswith("/login?next=")
    r = c.post("/signup", {"student_no": "20261234", "name": "새내기", "password": "abcd", "password2": "abcd", "next": path})
    assert r["Location"] == path
    rid = c.jpost("/api/reservations", {"seat_no": no("B-4"), "qr_token": qr_token(no("B-4"))}).json()["id"]
    assert Reservation.objects.get(id=rid).status == "in_use"


def test_16_change_seat(clock, ua):
    """이용 중 자리를 옮기고 싶음 → 옮길 좌석 QR → [A-1 → B-2 바꾸기] → 기존 좌석 반납, 새 좌석 바로 이용."""
    rid = reserve(ua, "A-1", qr=True)
    d = ua.jget(f"/api/seats/{no('B-2')}?t={qr_token(no('B-2'))}")
    assert d["page_mode"] == "reserve_now" and d["my_reservation"]["seat_label"] == "A-1"
    assert ua.jpost(f"/api/reservations/{rid}/return").status_code == 200
    new = reserve(ua, "B-2", qr=True)
    assert Reservation.objects.get(id=rid).status == "returned" and Reservation.objects.get(id=new).status == "in_use"
    assert not Alert.objects.filter(resolved_at__isnull=True).exists()

