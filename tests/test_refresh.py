from conftest import admin_seat, set_state

from seats.models import Alert, Reservation, StatusLog, User


def _logs(no):
    return list(StatusLog.objects.filter(seat_no=no).order_by("id").values_list("detail", flat=True))


def _open(no):
    return list(Alert.objects.filter(seat_id=no, resolved_at__isnull=True).order_by("id").values_list("type", flat=True))


def _in_use(no, now, student_no="20260001"):
    return Reservation.objects.create(user=User.objects.get(student_no=student_no), seat_id=no, status="in_use",
                                      start_at=now, end_at=now + 7200, checked_in_at=now)


def test_initial_layout_20_seats(admin):
    data = admin.jget("/api/admin/seats")
    assert len(data["seats"]) == 20
    seats = {s["label"]: s for s in data["seats"]}
    assert seats["A-3"]["detail"] == "unauthorized"            # seats.json 초기 배분
    assert seats["A-5"]["detail"] == "using" and not seats["A-5"]["needs_action"]
    assert seats["E-3"]["detail"] == "broken" and seats["E-3"]["note"] == "콘센트 고장"
    assert data["summary"] == {"available": 17, "in_use": 2, "unavailable": 1, "issues": 1}
    assert {f["kind"] for f in data["fixtures"]} >= {"window", "table", "door", "desk"}


def test_log_only_on_transition(admin):
    for _ in range(3):
        admin.get("/api/admin/seats")
    assert _logs(1) == ["empty"]
    set_state(admin, 1, "using")
    set_state(admin, 1, "item")
    assert _logs(1) == ["empty", "using", "item"]


def test_admin_using_does_not_need_action(admin):
    """관리자가 '이용 중'을 부여해도 처리 필요로 빠지지 않는다."""
    set_state(admin, 1, "using")
    s = admin_seat(admin, 1)
    assert s["seat_state"] == "in_use" and s["detail"] == "using" and not s["needs_action"]
    assert _open(1) == []


def test_alert_created_once_and_auto_resolved(admin):
    set_state(admin, 1, "unauthorized")
    for _ in range(3):
        admin.get("/api/admin/seats")
    assert _open(1) == ["unauthorized"]
    set_state(admin, 1, "using")  # 확인해 보니 정상 이용
    assert _open(1) == []
    a = Alert.objects.get(seat_id=1)
    assert a.resolution == "auto" and a.resolved_by is None


def test_handled_alert_not_recreated(admin):
    set_state(admin, 2, "unauthorized")
    a = next(x for x in admin.jget("/api/admin/alerts?open=1")["alerts"] if x["seat_no"] == 2)
    assert admin.jpost(f"/api/admin/alerts/{a['id']}/resolve", {"memo": "퇴실 안내"}).status_code == 200
    admin.get("/api/admin/seats")
    assert _open(2) == []


def test_away_after_limit(admin, clock):
    set_state(admin, 4, "using")
    _in_use(4, clock())
    set_state(admin, 4, "empty")
    s = admin_seat(admin, 4)
    assert s["detail"] == "away_short" and s["deadline_sec"] == 30 * 60
    clock.advance(30 * 60)
    assert admin_seat(admin, 4)["detail"] == "away" and _open(4) == ["away"]
    set_state(admin, 4, "using")  # 돌아옴
    assert admin_seat(admin, 4)["detail"] == "using" and _open(4) == []


def test_item_is_sub_state_then_hoarding(admin, clock):
    _in_use(6, clock())
    set_state(admin, 6, "item")
    s = admin_seat(admin, 6)
    assert s["seat_state"] == "in_use" and s["detail"] == "item" and not s["needs_action"]
    clock.advance(30 * 60)
    s = admin_seat(admin, 6)
    assert s["detail"] == "hoarding" and s["alert_id"]


def test_seat_unavailable_with_reservation(admin, user):
    user.jpost("/api/reservations", {"seat_no": 6})
    set_state(admin, 6, "broken", note="콘센트 고장")
    s = admin_seat(admin, 6)
    assert s["seat_state"] == "unavailable" and s["detail"] == "seat_unavailable" and _open(6) == ["seat_unavailable"]


def test_no_show_sweep_alert(user, clock, admin):
    rid = user.jpost("/api/reservations", {"seat_no": 1}).json()["id"]
    clock.advance(15 * 60 + 1)
    admin.get("/api/admin/seats")
    assert Reservation.objects.get(id=rid).status == "no_show" and _open(1) == ["no_show"]


def test_item_approval_ends_with_reservation(admin, user, clock):
    """예약 중 '짐만 있음'으로 확인한 짐이 예약 종료 뒤에도 남으면 무단 점유."""
    from conftest import qr_token
    rid = user.jpost("/api/reservations", {"seat_no": 7, "qr_token": qr_token(7)}).json()["id"]
    set_state(admin, 7, "item")
    user.jpost(f"/api/reservations/{rid}/return")
    assert admin_seat(admin, 7)["detail"] == "unauthorized"


def test_mark_cleared_on_expiry(admin, clock):
    r = _in_use(8, clock())
    set_state(admin, 8, "item")
    for _ in range(2):  # 관리자 모드는 60분 무사용 시 꺼지므로 중간에 한 번씩 사용
        clock.advance(2400)
        admin.get("/api/admin/seats")
    clock.advance(2400)
    assert admin_seat(admin, 8)["detail"] == "unauthorized"
    assert Reservation.objects.get(id=r.id).status == "expired"
