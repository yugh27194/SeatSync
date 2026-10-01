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
    assert data["summary"] == {"available": 17, "in_use": 2, "unavailable": 1, "issues": 1, "checks": 1}
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


def test_no_show_marks_check_until_admin_cancels(user, clock, admin):
    """체크인 시간 안에 아무도 오지 않으면 자동 취소 대신 '미입실'(확인 필요), 관리자가 취소한다."""
    rid = user.jpost("/api/reservations", {"seat_no": 1}).json()["id"]
    clock.advance(15 * 60 + 1)
    s = admin_seat(admin, 1)
    assert s["detail"] == "no_show" and s["check"] and s["seat_state"] == "in_use" and _open(1) == ["no_show"]
    assert Reservation.objects.get(id=rid).status == "reserved"
    assert user.jget("/api/seats")["my_status"]["detail"] == "no_show"
    admin.jpost(f"/api/admin/reservations/{rid}/force-return", {"no_show": True})
    assert Reservation.objects.get(id=rid).status == "no_show" and _open(1) == []
    assert admin_seat(admin, 1)["detail"] == "empty"


def test_late_arrival_can_still_check_in(user, clock, admin):
    from conftest import qr_token
    rid = user.jpost("/api/reservations", {"seat_no": 1}).json()["id"]
    clock.advance(20 * 60)
    assert user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(1)}).status_code == 200
    assert admin_seat(admin, 1)["detail"] == "using" and _open(1) == []


def test_reserved_until_end_becomes_no_show(user, clock):
    rid = user.jpost("/api/reservations", {"seat_no": 1}).json()["id"]
    clock.advance(121 * 60)
    user.get("/api/seats")
    assert Reservation.objects.get(id=rid).status == "no_show"


def test_item_approval_ends_with_reservation(admin, user, clock):
    """예약 중 '짐만 있음'으로 확인한 짐이 예약 종료 뒤에도 남으면(무단 점유 기준 시간 경과) 무단 점유."""
    from conftest import qr_token
    rid = user.jpost("/api/reservations", {"seat_no": 7, "qr_token": qr_token(7)}).json()["id"]
    clock.advance(60)
    set_state(admin, 7, "item")
    user.jpost(f"/api/reservations/{rid}/return")
    s = admin_seat(admin, 7)
    assert s["detail"] == "item" and s["check"] and not s["needs_action"]
    clock.advance(10 * 60)
    assert admin_seat(admin, 7)["detail"] == "unauthorized"


def test_mark_cleared_on_expiry(admin, clock):
    admin.jput("/api/admin/settings", {"auto_return_min": 0})  # 자동 반납 없이 시간 만료까지 보기
    r = _in_use(8, clock())
    set_state(admin, 8, "item")
    for _ in range(2):  # 관리자 모드는 60분 무사용 시 꺼지므로 중간에 한 번씩 사용
        clock.advance(2400)
        admin.get("/api/admin/seats")
    clock.advance(2400)
    assert admin_seat(admin, 8)["detail"] == "unauthorized"
    assert Reservation.objects.get(id=r.id).status == "expired"


# ---------------------------------------------------------------- 자동 강제 반납

def test_auto_return_after_away(admin, clock):
    """장기 이석으로 표시된 뒤 auto_return_min(기본 15분)이 지나면 자동 강제 반납."""
    r = _in_use(1, clock())
    set_state(admin, 1, "empty")                 # 자리 비움
    clock.advance(30 * 60)                       # 장기 이석 기준 30분
    assert admin_seat(admin, 1)["detail"] == "away"
    clock.advance(15 * 60 - 1)
    assert Reservation.objects.get(id=r.id).status == "in_use"
    clock.advance(1)
    s = admin_seat(admin, 1)
    assert Reservation.objects.get(id=r.id).status == "force_returned" and s["detail"] == "empty"
    assert _open(1) == []
    log = admin.jget("/api/admin/log?limit=5")["log"]
    assert log[0]["action"] == "auto_return" and "장기 이석" in log[0]["memo"]


def test_auto_return_no_show(user, clock):
    rid = user.jpost("/api/reservations", {"seat_no": 1}).json()["id"]
    clock.advance(15 * 60)                       # 체크인 제한 → 미입실
    user.get("/api/seats")
    assert Reservation.objects.get(id=rid).status == "reserved"
    clock.advance(15 * 60)                       # + 자동 반납 15분
    d = user.jget("/api/seats")
    assert Reservation.objects.get(id=rid).status == "no_show" and d["my_reservation"] is None


def test_auto_return_off_and_seconds(admin, clock):
    # 15초 단위 설정: 체크인 제한 30초, 자동 반납 45초
    r = admin.jput("/api/admin/settings", {"checkin_limit_min": 0.5, "auto_return_min": 0.75})
    assert r.status_code == 200 and r.json()["settings"]["checkin_limit_min"] == 0.5
    assert admin.jput("/api/admin/settings", {"checkin_limit_min": 0.3}).status_code == 400   # 15초 단위 아님
    res = Reservation.objects.create(user=User.objects.get(student_no="userC"), seat_id=2, status="reserved",
                                     start_at=clock(), end_at=clock() + 7200)
    clock.advance(30)
    assert admin_seat(admin, 2)["detail"] == "no_show"
    clock.advance(44)
    assert Reservation.objects.get(id=res.id).status == "reserved"
    clock.advance(1)
    admin.get("/api/admin/seats")
    assert Reservation.objects.get(id=res.id).status == "no_show"


def test_no_auto_return_while_camera_stale(admin, device, clock):
    from test_camera import post, snapshot
    r = _in_use(1, clock())
    post(device, snapshot(clock, {"A01": ("EMPTY", 0.0, 1)}))      # 비움 감지 후 카메라 끊김
    clock.advance(30 * 60 + 15 * 60 + 60)
    s = admin_seat(admin, 1)
    assert s["stale"] and Reservation.objects.get(id=r.id).status == "in_use"
