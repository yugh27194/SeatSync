from conftest import err, qr_token, set_state, user_id

from seats.models import Reservation, Seat


def reserve(client, seat_no, token=None):
    body = {"seat_no": seat_no}
    if token:
        body["qr_token"] = token
    return client.jpost("/api/reservations", body)


def _state(no):
    return Seat.objects.get(no=no).state


def test_reserve_success(user):
    r = reserve(user, 1)
    assert r.status_code == 201
    d = r.json()
    assert d["status"] == "reserved" and d["seat_label"] == "A-1" and d["checkin_deadline"]
    seats = user.jget("/api/seats")
    assert next(s for s in seats["seats"] if s["no"] == 1)["view"] == "mine"


def test_user_sees_three_states_without_marks(user, user2):
    reserve(user2, 2)
    data = user.jget("/api/seats")
    views = {s["no"]: s["view"] for s in data["seats"]}
    assert views[1] == "available" and views[2] == "taken" and views[3] == "taken" and views[20] == "unavailable"
    # 일반 사용자: 색은 3가지(무단 점유도 'taken'), 왜 사용중·사용불가인지는 알려 주지 않는다
    for s in data["seats"]:
        assert set(s) == {"no", "label", "x", "y", "zone", "booth", "view"}
    for word in ("unauthorized", "처리 필요", "확인 필요", "무단 점유", "고장"):
        assert word not in str(data)


def test_admin_sees_marks_only_in_admin_tab(admin):
    """관리자 코드를 통과해도 좌석 지도(/api/seats)는 일반 화면. 확인 필요 표시는 관리자 탭(/api/admin/seats)에만."""
    assert all("attention" not in s for s in admin.jget("/api/seats")["seats"])
    seats = admin.jget("/api/admin/seats")["seats"]
    assert {s["no"] for s in seats if s["needs_action"]} == {3}
    assert next(s for s in seats if s["no"] == 20)["detail_label"] == "고장"


def test_check_marks_away_and_item(admin):
    """이석·짐만 있음은 처리 필요(조치 목록) 전이라도 지도에 '!'(확인 필요)로 강조한다."""
    from conftest import T0, set_state
    from seats.models import Reservation, Seat, User
    Reservation.objects.create(user=User.objects.get(student_no="userC"), seat_id=1, status="in_use",
                               start_at=T0, end_at=T0 + 7200, checked_in_at=T0)
    Seat.objects.filter(no=1).update(state="empty")    # 이용 중 예약자가 잠시 자리 비움
    set_state(admin, 2, "item")                        # 예약 없이 짐만 있음(관리자 확인) → 무단 점유 아님
    seats = {s["no"]: s for s in admin.jget("/api/admin/seats")["seats"]}
    assert seats[1]["detail"] == "away_short" and seats[1]["check"] and not seats[1]["needs_action"]
    assert seats[2]["detail"] == "item" and seats[2]["check"] and not seats[2]["needs_action"]
    assert seats[1]["detail_desc"] and seats[2]["detail_desc"]
    assert not seats[5]["check"]                       # 정상 이용 중
    assert seats[1]["category_label"] == "이석(일시)" and seats[2]["category"] == "away"


def test_layout_in_user_api(user):
    data = user.jget("/api/seats")
    assert data["grid"] == {"cols": 5, "rows": 12} and len(data["seats"]) == 20
    assert {s["label"] for s in data["seats"] if s["booth"]} == {"D-1", "D-2", "D-3", "D-4"}


def test_conflicts(user, user2):
    assert reserve(user, 1).status_code == 201
    assert err(reserve(user2, 1)) == "SEAT_TAKEN"
    assert err(reserve(user, 2)) == "ALREADY_HAS_RESERVATION"
    assert reserve(user, 99).status_code == 404


def test_unavailable_seat(user):
    r = reserve(user, 20)
    assert r.status_code == 409 and err(r) == "SEAT_UNAVAILABLE"


def test_occupied_needs_qr(user):
    assert err(reserve(user, 3)) == "SEAT_OCCUPIED"      # A-3: 초기 배분 '무단 점유'
    assert err(reserve(user, 3, "wrong")) == "SEAT_OCCUPIED"
    r = reserve(user, 3, qr_token(3))
    assert r.status_code == 201 and r.json()["status"] == "in_use"


def test_qr_checkin_sets_occupied_and_return_empties(user):
    rid = reserve(user, 1).json()["id"]
    assert _state(1) == "empty"
    r = user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(1)})
    assert r.status_code == 200 and _state(1) == "occupied"
    assert user.jpost(f"/api/reservations/{rid}/return").json()["status"] == "returned"
    assert _state(1) == "empty"


def test_checkin_errors(user, user2):
    rid = reserve(user, 1).json()["id"]
    assert err(user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": "bad"})) == "BAD_QR_TOKEN"
    assert user2.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(1)}).status_code == 404
    user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(1)})
    assert err(user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(1)})) == "INVALID_STATE"


def test_checkin_blocked_when_seat_unavailable(user, admin):
    rid = reserve(user, 1).json()["id"]
    set_state(admin, 1, "maintenance")
    assert err(user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(1)})) == "SEAT_UNAVAILABLE"


def test_extend_window_and_limit(user, clock):
    rid = reserve(user, 1, qr_token(1)).json()["id"]
    r = user.jpost(f"/api/reservations/{rid}/extend")
    assert err(r) == "EXTEND_NOT_ALLOWED" and "30분" in r.json()["error"]["message"]
    for _ in range(2):
        clock.t = Reservation.objects.get(id=rid).end_at - 10 * 60
        assert user.jpost(f"/api/reservations/{rid}/extend").status_code == 200
    clock.t = Reservation.objects.get(id=rid).end_at - 10 * 60
    assert "최대 2회" in user.jpost(f"/api/reservations/{rid}/extend").json()["error"]["message"]


def test_cancel_and_expire(user, clock):
    rid = reserve(user, 1).json()["id"]
    assert user.jpost(f"/api/reservations/{rid}/return").json()["status"] == "cancelled"
    rid = reserve(user, 2, qr_token(2)).json()["id"]
    clock.advance(120 * 60)
    user.get("/api/seats")
    assert Reservation.objects.get(id=rid).status == "expired"


def test_suspended_user_cannot_reserve(user, admin):
    admin.jpost(f"/api/admin/users/{user_id('20260001')}/suspend", {"days": 3})
    r = reserve(user, 1)
    assert r.status_code == 403 and err(r) == "SUSPENDED"
    assert user.jget("/api/seats")["me"]["suspended_until"]


def test_seat_page_modes(user, user2):
    tok = qr_token(1)
    d = user.jget(f"/api/seats/1?t={tok}")
    assert d["page_mode"] == "reserve_now" and d["qr_ok"] is True
    assert user.jget("/api/seats/1?t=bad")["qr_ok"] is False
    d = user.jget("/api/seats/20")
    assert d["page_mode"] == "unavailable" and "고장" not in str(d)  # 사용불가 사유는 이용자에게 보이지 않는다
    assert user.jget("/api/seats/3")["occupied"] is True
    rid = reserve(user, 1).json()["id"]
    assert user.jget("/api/seats/1")["page_mode"] == "mine_checkin"
    assert user2.jget("/api/seats/1")["page_mode"] == "reserved_by_other"
    user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": tok})
    assert user.jget("/api/seats/1")["page_mode"] == "mine_in_use"


def test_call_dedup(user, clock):
    assert user.jpost("/api/calls", {"seat_no": 3, "memo": "제 자리에 다른 분이 계세요"}).status_code == 201
    clock.advance(30)
    dup = user.jpost("/api/calls", {"seat_no": 3})
    assert dup.status_code == 200 and dup.json()["duplicate"] is True
    clock.advance(31)
    assert user.jpost("/api/calls", {"seat_no": 3}).status_code == 201


def test_csrf_enforced_for_browser_posts(user):
    from django.test import Client
    c = Client(enforce_csrf_checks=True)
    c.post("/login", {"student_no": "userA", "password": "1234"})
    assert c.post("/api/reservations", data='{"seat_no": 1}', content_type="application/json").status_code == 403
