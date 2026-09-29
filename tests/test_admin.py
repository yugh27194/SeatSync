"""관리자 조치: 세부 상태 부여, 상황별 조치, 경고·정지, 시연 상황."""
from conftest import admin_seat, err, qr_token, set_state, user_id

from seats.models import Alert, Reservation, User


def _alert(admin, no, type_):
    return next(a for a in admin.jget("/api/admin/alerts?open=1")["alerts"] if a["seat_no"] == no and a["type"] == type_)


def _actions(admin):
    return [x["action"] for x in admin.jget("/api/admin/log")["log"]]


def test_permissions(user):
    r = user.get("/api/admin/seats")
    assert r.status_code == 403 and r.json()["error"]["message"] == "관리자 권한이 필요합니다."
    assert user.jpost("/api/admin/demo").status_code == 403
    assert user.jpost("/api/admin/seats/1/state", {"detail": "empty"}).status_code == 403


def test_assign_options_grouped(admin):
    groups = admin.jget("/api/admin/seats")["assign"]["groups"]
    assert [g["label"] for g in groups] == ["빈자리", "사용중", "사용불가"]
    in_use = [i["label"] for i in groups[1]["items"]]
    assert in_use == ["이용 중", "짐만 있음", "무단 점유", "이탈", "사석화"]
    assert [i["label"] for i in groups[2]["items"]] == ["고장", "점검·청소", "사용 중지"]


def test_seat_state_assignment(admin):
    assert admin.jpost("/api/admin/seats/1/state", {"detail": "bogus"}).status_code == 400
    set_state(admin, 1, "maintenance", note="청소 중")
    s = admin_seat(admin, 1)
    assert s["seat_state"] == "unavailable" and s["detail"] == "maintenance" and s["note"] == "청소 중"
    set_state(admin, 1, "empty")
    assert admin_seat(admin, 1)["note"] is None
    assert _actions(admin)[:2] == ["seat_state", "seat_state"]


def test_away_hoarding_require_in_use_reservation(admin, user_a):
    r = admin.jpost("/api/admin/seats/1/state", {"detail": "away"})
    assert r.status_code == 409 and "이용 중인 예약" in r.json()["error"]["message"]
    user_a.jpost("/api/reservations", {"seat_no": 1, "qr_token": qr_token(1)})
    set_state(admin, 1, "hoarding")
    s = admin_seat(admin, 1)
    assert s["detail"] == "hoarding" and s["needs_action"] and s["mark"] == "issue"
    set_state(admin, 1, "away")
    assert admin_seat(admin, 1)["detail"] == "away"


def test_unauthorized_on_site_assignment(admin):
    """무단 점유(A-3) → 앉아 있는 이용자에게 현장 배정."""
    assert admin_seat(admin, 3)["detail"] == "unauthorized"
    uid = user_id("userA")
    assert admin.jpost("/api/admin/reservations", {"user_id": uid, "seat_no": 3, "checkin": True}).status_code == 201
    s = admin_seat(admin, 3)
    assert s["detail"] == "using" and s["reservation"]["user"]["name"] == "사용자A"
    assert s["reservation"]["source"] == "admin"
    assert err(admin.jpost("/api/admin/reservations", {"user_id": uid, "seat_no": 1})) == "ALREADY_HAS_RESERVATION"
    uid_b = user_id("userB")
    assert err(admin.jpost("/api/admin/reservations", {"user_id": uid_b, "seat_no": 20})) == "SEAT_UNAVAILABLE"
    assert err(admin.jpost("/api/admin/reservations", {"user_id": uid_b, "seat_no": 3})) == "SEAT_TAKEN"


def test_unauthorized_asked_to_leave(admin):
    assert admin_seat(admin, 3)["detail"] == "unauthorized"
    set_state(admin, 3, "empty")
    assert admin_seat(admin, 3)["seat_state"] == "available"
    assert Alert.objects.get(seat_id=3).resolution == "auto"


def test_no_checkin_proxy_checkin(admin, user_a, device, clock):
    from conftest import send
    rid = user_a.jpost("/api/reservations", {"seat_no": 1}).json()["id"]
    send(device, clock, {1: ("person", clock())})  # 카메라: 누군가 앉았지만 체크인 안 함
    assert admin_seat(admin, 1)["detail"] == "no_checkin"
    assert admin.jpost(f"/api/admin/reservations/{rid}/checkin").status_code == 200
    s = admin_seat(admin, 1)
    assert s["detail"] == "using" and s["reservation"]["status"] == "in_use"
    assert err(admin.jpost(f"/api/admin/reservations/{rid}/checkin")) == "INVALID_STATE"


def test_admin_marks_reserved_seat_seated(admin, user_a):
    """관리자가 예약석에 '이용 중'을 부여하면 체크인 누락이 아닌 '착석(체크인 전)'."""
    user_a.jpost("/api/reservations", {"seat_no": 1})
    set_state(admin, 1, "using")
    s = admin_seat(admin, 1)
    assert s["detail"] == "seated_unchecked" and not s["needs_action"]


def test_move_reserver_away_from_stranger(admin, user_a):
    rid = user_a.jpost("/api/reservations", {"seat_no": 1}).json()["id"]
    set_state(admin, 1, "unauthorized")
    assert err(admin.jpost(f"/api/admin/reservations/{rid}/move", {"seat_no": 3})) == "SEAT_OCCUPIED"
    assert err(admin.jpost(f"/api/admin/reservations/{rid}/move", {"seat_no": 20})) == "SEAT_UNAVAILABLE"
    assert admin.jpost(f"/api/admin/reservations/{rid}/move", {"seat_no": 2}).status_code == 200
    assert admin_seat(admin, 2)["reservation"]["id"] == rid
    assert admin_seat(admin, 1)["detail"] == "unauthorized"
    log = admin.jget("/api/admin/log")["log"][0]
    assert log["action"] == "move" and log["memo"].startswith("A-1 → A-2") and log["admin_name"] == "테스트5"


def test_seat_unavailable_move_in_use(admin, user_a):
    rid = user_a.jpost("/api/reservations", {"seat_no": 1, "qr_token": qr_token(1)}).json()["id"]
    set_state(admin, 1, "broken", note="누수")
    assert admin_seat(admin, 1)["detail"] == "seat_unavailable"
    admin.jpost(f"/api/admin/reservations/{rid}/move", {"seat_no": 2})
    a1, a2 = admin_seat(admin, 1), admin_seat(admin, 2)
    assert a1["detail"] == "broken" and not a1["needs_action"]
    assert a2["detail"] == "using" and a2["actual"] == "occupied"


def test_hoarding_force_return_then_collect(admin, user_b, clock):
    rid = user_b.jpost("/api/reservations", {"seat_no": 2, "qr_token": qr_token(2)}).json()["id"]
    set_state(admin, 2, "item")
    clock.advance(31 * 60)
    a = _alert(admin, 2, "hoarding")
    assert a["reservation"]["user"]["name"] == "사용자B"
    r = admin.jpost(f"/api/admin/users/{a['reservation']['user']['id']}/warn", {"alert_id": a["id"]})
    assert r.json()["warnings"] == 1
    assert admin.jpost(f"/api/admin/reservations/{rid}/force-return", {"memo": "사석화"}).status_code == 200
    assert Reservation.objects.get(id=rid).status == "force_returned"
    assert Alert.objects.get(id=a["id"]).resolution == "force_returned"
    # 짐이 남아 있으면 무단 점유 → 짐 수거 후 빈자리
    assert admin_seat(admin, 2)["detail"] == "unauthorized"
    set_state(admin, 2, "empty")
    assert admin_seat(admin, 2)["seat_state"] == "available"
    assert _actions(admin)[:3] == ["seat_state", "force_return", "warn"]


def test_force_return_clears_issue_mark(admin, user_a):
    rid = user_a.jpost("/api/reservations", {"seat_no": 1, "qr_token": qr_token(1)}).json()["id"]
    set_state(admin, 1, "away")
    admin.jpost(f"/api/admin/reservations/{rid}/force-return")
    assert admin_seat(admin, 1)["detail"] == "empty"


def test_no_show_warn_and_resolve(admin, user_a, clock):
    user_a.jpost("/api/reservations", {"seat_no": 1})
    clock.advance(16 * 60)
    a = _alert(admin, 1, "no_show")
    admin.jpost(f"/api/admin/users/{a['reservation']['user']['id']}/warn", {"alert_id": a["id"], "resolve": True})
    assert all(x["id"] != a["id"] for x in admin.jget("/api/admin/alerts?open=1")["alerts"])


def test_warning_limit_and_suspension(admin, user_a):
    uid = user_id("userA")
    for _ in range(3):
        r = admin.jpost(f"/api/admin/users/{uid}/warn", {"reason": "테스트"}).json()
    assert r["warnings"] == 3 and r["suspend_suggested"] is True
    assert next(x for x in admin.jget("/api/admin/users")["users"] if x["id"] == uid)["suspend_suggested"]
    assert admin.jpost(f"/api/admin/users/{uid}/unwarn").json()["warnings"] == 2
    assert admin.jpost(f"/api/admin/users/{uid}/suspend", {"days": 999}).status_code == 400
    assert admin.jpost(f"/api/admin/users/{uid}/suspend", {"days": 7, "reason": "반복 위반"}).status_code == 200
    assert err(user_a.jpost("/api/reservations", {"seat_no": 1})) == "SUSPENDED"
    assert err(admin.jpost("/api/admin/reservations", {"user_id": uid, "seat_no": 1})) == "SUSPENDED"
    assert admin.jpost(f"/api/admin/users/{uid}/unsuspend").status_code == 200
    assert user_a.jpost("/api/reservations", {"seat_no": 1}).status_code == 201


def test_admin_extend(admin, user_a):
    rid = user_a.jpost("/api/reservations", {"seat_no": 1}).json()["id"]
    before = Reservation.objects.get(id=rid)
    admin.jpost(f"/api/admin/reservations/{rid}/extend")
    after = Reservation.objects.get(id=rid)
    assert after.end_at == before.end_at + 3600 and after.extend_count == before.extend_count


def test_demo_scenario(admin):
    r = admin.jpost("/api/admin/demo")
    assert r.status_code == 200 and len(r.json()["messages"]) == 13
    seats = {s["label"]: s for s in admin.jget("/api/admin/seats")["seats"]}
    expect = {
        "A-1": "using", "A-2": "away", "A-3": "unauthorized", "A-4": "no_checkin", "A-5": "using",
        "B-1": "seat_unavailable", "B-2": "hoarding", "B-3": "no_show", "B-4": "item", "C-1": "waiting",
        "C-2": "unauthorized", "D-1": "maintenance", "E-3": "broken", "D-4": "empty",
    }
    assert {k: seats[k]["detail"] for k in expect} == expect
    assert seats["A-1"]["reservation"]["user"]["name"] == "사용자A"
    assert seats["A-5"]["reservation"] is None and not seats["A-5"]["needs_action"]
    types = sorted(a["type"] for a in admin.jget("/api/admin/alerts?open=1")["alerts"])
    assert types == ["away", "hoarding", "no_checkin", "no_show", "seat_unavailable", "unauthorized", "unauthorized"]
    admin.jpost("/api/admin/demo")  # 다시 배치해도 같은 결과
    assert len(admin.jget("/api/admin/alerts?open=1")["alerts"]) == 7
    assert User.objects.count() == 8


def test_settings(admin, user):
    assert admin.jput("/api/admin/settings", {"nope": 1}).status_code == 400
    assert admin.jput("/api/admin/settings", {"away_limit_min": 0}).status_code == 400
    assert admin.jput("/api/admin/settings", {"away_limit_min": 5}).json()["settings"]["away_limit_min"] == 5
    assert user.jput("/api/admin/settings", {"away_limit_min": 5}).status_code == 403
    assert "settings" in _actions(admin)
