"""실제 배치(config/seats.json, 8석: 왼쪽 A-1~A-4 · 오른쪽 B-1~B-4)로 동작하는지 확인."""
import pytest
from conftest import T0
from test_camera import post, snapshot

from seats.models import ACTIVE, Alert, Reservation, Seat, User, WaitEntry
from seats.seed import seed


@pytest.fixture
def layout8(settings):
    settings.SEATSYNC = {**settings.SEATSYNC, "SEATS_FILE": settings.BASE_DIR / "config" / "seats.json"}


def test_eight_seats_left_and_right(layout8, admin):
    seed(now=T0)
    data = admin.jget("/api/seats")
    seats = {s["label"]: s for s in data["seats"]}
    assert data["grid"] == {"cols": 5, "rows": 2} and sorted(seats) == ["A-1", "A-2", "A-3", "A-4", "B-1", "B-2", "B-3", "B-4"]
    assert data["fixtures"] == []  # 창문·통로·벽·출입문·안내데스크 없이 좌석만
    # A1 A2 | B1 B2
    # A3 A4 | B3 B4
    pos = {k: (v["x"], v["y"]) for k, v in seats.items()}
    assert pos == {"A-1": (1, 1), "A-2": (2, 1), "A-3": (1, 2), "A-4": (2, 2),
                   "B-1": (4, 1), "B-2": (5, 1), "B-3": (4, 2), "B-4": (5, 2)}
    # 20석 배치에서 넘어오면 9~20번은 비활성, 1~8번은 새 이름·위치로 바뀐다
    assert Seat.objects.filter(active=True).count() == 8
    assert Seat.objects.get(no=5).label == "B-1"


def test_fresh_eight_seats_start_empty(layout8, db):
    Seat.objects.all().delete()
    seed(now=T0)
    assert set(Seat.objects.values_list("state", flat=True)) == {"empty"}


def test_removed_seats_release_reservations(layout8, admin, user):
    # 20석 배치에서 12번 좌석을 예약·대기 중인 상태로 8석으로 줄인다
    assert user.jpost("/api/reservations", {"seat_no": 12}).status_code in (200, 201)
    WaitEntry.objects.create(user=User.objects.get(student_no="userB"), zone="집중석", created_at=T0)  # 없어질 구역
    seed(now=T0)
    assert not Reservation.objects.filter(seat_id=12, status__in=ACTIVE).exists()
    assert not WaitEntry.objects.filter(user=User.objects.get(student_no="userB"), status__in=("waiting", "offered")).exists()
    assert not Alert.objects.filter(seat_id__gt=8, resolved_at__isnull=True).exists()
    assert user.jget("/api/seats")["my_reservation"] is None
    assert user.jpost("/api/reservations", {"seat_no": 12}).status_code >= 400   # 없어진 좌석은 예약 불가


def test_demo_on_eight_seats(layout8, admin):
    from seats.services import setup_demo
    seed(now=T0)
    assert len(setup_demo(T0)) == 8
    seats = {s["label"]: s for s in admin.jget("/api/admin/seats")["seats"]}
    expect = {"A-1": "using", "A-2": "away", "A-3": "unauthorized", "A-4": "detected",
              "B-1": "item", "B-2": "hoarding", "B-3": "unknown", "B-4": "broken"}
    assert {k: seats[k]["detail"] for k in expect} == expect
    assert {k for k, s in seats.items() if s["check"]} == {"A-2", "A-3", "B-1", "B-2", "B-3"}
    assert seats["B-3"]["stale"] and seats["B-3"]["seat_state"] == "in_use"


def test_one_camera_covers_eight_seats(layout8, device, clock, admin):
    seed(now=T0)
    ids = {f"A0{i}": ("OCCUPIED" if i == 5 else "EMPTY", 0.9, 3) for i in range(1, 9)}
    r = post(device, snapshot(clock, ids)).json()
    assert r["accepted"] == 8 and not r["missing"] and not r["ignored"]
    assert Seat.objects.get(label="B-1").state == "occupied"   # A05 = 오른쪽 첫 자리
    cfg = device.get("/api/device/config?camera_id=cam1", HTTP_X_DEVICE_KEY="test-key").json()
    assert [s["camera_seat"] for s in cfg["seats"]] == [f"A0{i}" for i in range(1, 9)]


def test_four_categories_on_admin_map(layout8, admin):
    """관리자 상태 분류: 정상(초록) · 이석(주황, 일시/장기) · 무단 점유(빨강) · 판단 불가(짙은 회색)."""
    from seats.services import setup_demo
    seed(now=T0)
    setup_demo(T0)
    d = admin.jget("/api/admin/seats")
    seats = {s["label"]: s for s in d["seats"]}
    cats = {k: (s["category"], s["category_label"]) for k, s in seats.items()}
    assert cats == {
        "A-1": ("normal", "정상"), "A-2": ("away", "이석(장기)"), "A-3": ("unauthorized", "무단 점유"),
        "A-4": ("normal", "정상"), "B-1": ("away", "이석(일시)"), "B-2": ("away", "이석(장기)"),
        "B-3": ("unknown", "판단 불가"), "B-4": ("unavailable", "사용불가"),
    }
    c = d["categories"]
    assert (c["normal"], c["away"], c["away_short"], c["away_long"], c["unauthorized"], c["unknown"]) == (2, 3, 1, 2, 1, 1)
