"""관리자 상황별 조치."""
from conftest import admin_seat, set_state, user_id


def err(r):
    return r.get_json()["error"]["code"]


def _alert(admin, seat_no, type_):
    alerts = admin.get("/api/admin/alerts?open=1").get_json()["alerts"]
    return next(a for a in alerts if a["seat_no"] == seat_no and a["type"] == type_)


def _log_actions(admin):
    return [x["action"] for x in admin.get("/api/admin/log").get_json()["log"]]


def test_permissions(user):
    assert user.get("/api/admin/seats").status_code == 403
    assert user.post("/api/admin/demo").status_code == 403
    assert user.post("/api/admin/seats/1/state", json={"state": "empty"}).status_code == 403


def test_seat_state_assignment(admin):
    assert admin.post("/api/admin/seats/1/state", json={"state": "bogus"}).status_code == 400
    set_state(admin, 1, "unavailable", note="청소 중")
    s = admin_seat(admin, 1)
    assert s["seat_state"] == "unavailable" and s["actual_note"] == "청소 중" and s["actual_source"] == "manual"
    set_state(admin, 1, "empty")
    assert admin_seat(admin, 1)["actual_note"] is None
    assert _log_actions(admin)[:2] == ["seat_state", "seat_state"]


def test_unauthorized_on_site_assignment(admin, conn):
    """미예약 사용(A-3) → 앉아 있는 이용자에게 현장 배정."""
    assert admin_seat(admin, 3)["situation"] == "unauthorized"
    uid = user_id(conn, "userA")
    r = admin.post("/api/admin/reservations", json={"user_id": uid, "seat_no": 3, "checkin": True})
    assert r.status_code == 201
    s = admin_seat(admin, 3)
    assert s["situation"] == "ok" and s["reservation"]["user"]["name"] == "사용자A"
    assert s["reservation"]["status"] == "in_use" and s["reservation"]["source"] == "admin"
    # 이미 예약 있는 사용자 / 사용불가 좌석
    assert err(admin.post("/api/admin/reservations", json={"user_id": uid, "seat_no": 1})) == "ALREADY_HAS_RESERVATION"
    uid_b = user_id(conn, "userB")
    assert err(admin.post("/api/admin/reservations", json={"user_id": uid_b, "seat_no": 5})) == "SEAT_UNAVAILABLE"
    assert err(admin.post("/api/admin/reservations", json={"user_id": uid_b, "seat_no": 3})) == "SEAT_TAKEN"


def test_unauthorized_asked_to_leave(admin, conn):
    """미예약 사용 → 퇴실 안내 후 현장 상태를 빈자리로."""
    assert admin_seat(admin, 3)["situation"] == "unauthorized"
    set_state(admin, 3, "empty")
    assert admin_seat(admin, 3)["seat_state"] == "available"
    assert conn.execute("SELECT resolution FROM alerts WHERE seat_no=3").fetchone()["resolution"] == "auto"


def test_no_checkin_proxy_checkin(admin, user_a, conn):
    rid = user_a.post("/api/reservations", json={"seat_no": 1}).get_json()["id"]
    set_state(admin, 1, "occupied")
    assert admin_seat(admin, 1)["situation"] == "no_checkin"
    assert admin.post(f"/api/admin/reservations/{rid}/checkin").status_code == 200
    s = admin_seat(admin, 1)
    assert s["situation"] == "ok" and s["reservation"]["status"] == "in_use"
    assert err(admin.post(f"/api/admin/reservations/{rid}/checkin")) == "INVALID_STATE"


def test_no_checkin_stranger_move_reserver(admin, user_a, conn):
    """예약석에 다른 사람이 앉아 있음 → 예약자를 빈자리로 이동, 남은 사람은 미예약 사용으로 바뀜."""
    rid = user_a.post("/api/reservations", json={"seat_no": 1}).get_json()["id"]
    set_state(admin, 1, "occupied")
    assert err(admin.post(f"/api/admin/reservations/{rid}/move", json={"seat_no": 3})) == "SEAT_OCCUPIED"
    assert err(admin.post(f"/api/admin/reservations/{rid}/move", json={"seat_no": 5})) == "SEAT_UNAVAILABLE"
    assert admin.post(f"/api/admin/reservations/{rid}/move", json={"seat_no": 2}).status_code == 200
    assert admin_seat(admin, 2)["reservation"]["id"] == rid
    assert admin_seat(admin, 1)["situation"] == "unauthorized"
    log = admin.get("/api/admin/log").get_json()["log"][0]
    assert log["action"] == "move" and log["memo"].startswith("A-1 → A-2")


def test_seat_unavailable_move_in_use(admin, user_a, conn):
    from conftest import qr_token
    rid = user_a.post("/api/reservations", json={"seat_no": 1, "qr_token": qr_token(conn, 1)}).get_json()["id"]
    set_state(admin, 1, "unavailable", note="누수")
    assert admin_seat(admin, 1)["situation"] == "seat_unavailable"
    admin.post(f"/api/admin/reservations/{rid}/move", json={"seat_no": 2})
    a1, a2 = admin_seat(admin, 1), admin_seat(admin, 2)
    assert a1["seat_state"] == "unavailable" and a1["situation"] == "ok"
    assert a2["situation"] == "ok" and a2["actual"] == "occupied"


def test_away_force_return_and_warn(admin, user_b, conn, clock):
    from conftest import qr_token
    rid = user_b.post("/api/reservations", json={"seat_no": 2, "qr_token": qr_token(conn, 2)}).get_json()["id"]
    set_state(admin, 2, "empty")
    clock.advance(31 * 60)
    a = _alert(admin, 2, "away")
    assert a["reservation"]["user"]["name"] == "사용자B"
    uid = a["reservation"]["user"]["id"]
    r = admin.post(f"/api/admin/users/{uid}/warn", json={"alert_id": a["id"]})
    assert r.get_json()["warnings"] == 1
    assert admin.post(f"/api/admin/reservations/{rid}/force-return", json={"memo": "40분 자리 비움"}).status_code == 200
    assert conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()["status"] == "force_returned"
    assert conn.execute("SELECT resolution FROM alerts WHERE id=?", (a["id"],)).fetchone()["resolution"] == "force_returned"
    assert admin_seat(admin, 2)["seat_state"] == "available"
    assert _log_actions(admin)[:2] == ["force_return", "warn"]


def test_no_show_warn_and_resolve(admin, user_a, clock):
    user_a.post("/api/reservations", json={"seat_no": 1})
    clock.advance(16 * 60)
    a = _alert(admin, 1, "no_show")
    uid = a["reservation"]["user"]["id"]
    admin.post(f"/api/admin/users/{uid}/warn", json={"alert_id": a["id"], "resolve": True})
    assert all(x["id"] != a["id"] for x in admin.get("/api/admin/alerts?open=1").get_json()["alerts"])


def test_warning_limit_suggests_suspension(admin, conn, user_a):
    uid = user_id(conn, "userA")
    for i in range(3):
        r = admin.post(f"/api/admin/users/{uid}/warn", json={"reason": "테스트"}).get_json()
    assert r["warnings"] == 3 and r["suspend_suggested"] is True
    u = next(x for x in admin.get("/api/admin/users").get_json()["users"] if x["id"] == uid)
    assert u["suspend_suggested"] is True
    assert admin.post(f"/api/admin/users/{uid}/unwarn").get_json()["warnings"] == 2
    assert admin.post(f"/api/admin/users/{uid}/suspend", json={"days": 999}).status_code == 400
    assert admin.post(f"/api/admin/users/{uid}/suspend", json={"days": 7, "reason": "반복 위반"}).status_code == 200
    assert err(user_a.post("/api/reservations", json={"seat_no": 1})) == "SUSPENDED"
    assert err(admin.post("/api/admin/reservations", json={"user_id": uid, "seat_no": 1})) == "SUSPENDED"
    assert admin.post(f"/api/admin/users/{uid}/unsuspend").status_code == 200
    assert user_a.post("/api/reservations", json={"seat_no": 1}).status_code == 201


def test_admin_extend(admin, user_a, conn):
    rid = user_a.post("/api/reservations", json={"seat_no": 1}).get_json()["id"]
    before = conn.execute("SELECT end_at, extend_count FROM reservations WHERE id=?", (rid,)).fetchone()
    admin.post(f"/api/admin/reservations/{rid}/extend")
    after = conn.execute("SELECT end_at, extend_count FROM reservations WHERE id=?", (rid,)).fetchone()
    assert after["end_at"] == before["end_at"] + 60 * 60 and after["extend_count"] == before["extend_count"]


def test_demo_scenario(admin, conn):
    r = admin.post("/api/admin/demo")
    assert r.status_code == 200 and len(r.get_json()["messages"]) == 8
    seats = {s["label"]: s for s in admin.get("/api/admin/seats").get_json()["seats"]}
    assert seats["A-1"]["situation"] == "ok" and seats["A-1"]["reservation"]["user"]["name"] == "사용자A"
    assert seats["A-2"]["situation"] == "away"
    assert seats["A-3"]["situation"] == "unauthorized"
    assert seats["A-4"]["situation"] == "no_checkin" and seats["A-4"]["reservation"]["user"]["name"] == "사용자C"
    assert seats["B-1"]["seat_state"] == "unavailable" and seats["B-1"]["situation"] == "ok"
    assert seats["B-2"]["situation"] == "seat_unavailable"
    assert seats["B-3"]["situation"] == "ok" and seats["B-3"]["note"] == "잠시 자리 비움"
    assert seats["B-4"]["seat_state"] == "available"
    types = sorted(a["type"] for a in admin.get("/api/admin/alerts?open=1").get_json()["alerts"])
    assert types == ["away", "no_checkin", "no_show", "seat_unavailable", "unauthorized"]
    # 다시 배치해도 같은 결과(기존 예약·알림 정리)
    admin.post("/api/admin/demo")
    assert len(admin.get("/api/admin/alerts?open=1").get_json()["alerts"]) == 5


def test_settings(admin, user):
    assert admin.put("/api/admin/settings", json={"nope": 1}).status_code == 400
    assert admin.put("/api/admin/settings", json={"away_limit_min": 0}).status_code == 400
    assert admin.put("/api/admin/settings", json={"away_limit_min": 5}).get_json()["settings"]["away_limit_min"] == 5
    assert user.put("/api/admin/settings", json={"away_limit_min": 5}).status_code == 403
    assert "settings" in _log_actions(admin)
