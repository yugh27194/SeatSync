from conftest import qr_token, set_state


def err(r):
    return r.get_json()["error"]["code"]


def reserve(client, seat_no, token=None):
    body = {"seat_no": seat_no}
    if token:
        body["qr_token"] = token
    return client.post("/api/reservations", json=body)


def _state(conn, no):
    return conn.execute("SELECT state FROM seats WHERE no=?", (no,)).fetchone()["state"]


def test_reserve_success(user):
    r = reserve(user, 1)
    assert r.status_code == 201
    d = r.get_json()
    assert d["status"] == "reserved" and d["seat_label"] == "A-1" and d["checkin_deadline"]
    seats = user.get("/api/seats").get_json()
    assert next(s for s in seats["seats"] if s["no"] == 1)["view"] == "mine"


def test_user_sees_only_three_states(user, user2):
    reserve(user2, 2)
    data = user.get("/api/seats").get_json()
    views = {s["no"]: s["view"] for s in data["seats"]}
    attention = {s["no"] for s in data["seats"] if s["attention"]}
    assert attention == {3}  # 문제 좌석에는 '!' 표시만(사유는 숨김)
    assert views[1] == "available"
    assert views[2] == "taken"          # 예약
    assert views[3] == "taken"          # 예약 없이 사용중 → 사용자에겐 '예약(사용중)'
    assert views[5] == "unavailable"
    text = str(data)
    for word in ("unauthorized", "situation", "무단", "이탈", "사석화"):
        assert word not in text


def test_conflicts(user, user2):
    assert reserve(user, 1).status_code == 201
    assert err(reserve(user2, 1)) == "SEAT_TAKEN"
    assert err(reserve(user, 2)) == "ALREADY_HAS_RESERVATION"
    assert reserve(user, 99).status_code == 404


def test_unavailable_seat(user):
    r = reserve(user, 5)
    assert r.status_code == 409 and err(r) == "SEAT_UNAVAILABLE"


def test_occupied_needs_qr(user, conn):
    assert err(reserve(user, 3)) == "SEAT_OCCUPIED"      # A-3: 임시 배분 '사용중'
    assert err(reserve(user, 3, "wrong")) == "SEAT_OCCUPIED"
    r = reserve(user, 3, qr_token(conn, 3))
    assert r.status_code == 201 and r.get_json()["status"] == "in_use"


def test_qr_checkin_sets_occupied_and_return_empties(user, conn):
    rid = reserve(user, 1).get_json()["id"]
    assert _state(conn, 1) == "empty"
    r = user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)})
    assert r.status_code == 200 and _state(conn, 1) == "occupied"
    assert user.post(f"/api/reservations/{rid}/return").get_json()["status"] == "returned"
    assert _state(conn, 1) == "empty"


def test_checkin_errors(user, user2, conn):
    rid = reserve(user, 1).get_json()["id"]
    assert err(user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": "bad"})) == "BAD_QR_TOKEN"
    assert user2.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)}).status_code == 404
    user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)})
    assert err(user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)})) == "INVALID_STATE"


def test_checkin_blocked_when_seat_unavailable(user, admin, conn):
    rid = reserve(user, 1).get_json()["id"]
    set_state(admin, 1, "unavailable")
    assert err(user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)})) == "SEAT_UNAVAILABLE"


def test_extend_window_and_limit(user, conn, clock):
    rid = reserve(user, 1, qr_token(conn, 1)).get_json()["id"]
    r = user.post(f"/api/reservations/{rid}/extend")
    assert err(r) == "EXTEND_NOT_ALLOWED" and "30분" in r.get_json()["error"]["message"]
    for _ in range(2):
        end = conn.execute("SELECT end_at FROM reservations WHERE id=?", (rid,)).fetchone()["end_at"]
        clock.t = end - 10 * 60
        assert user.post(f"/api/reservations/{rid}/extend").status_code == 200
    end = conn.execute("SELECT end_at FROM reservations WHERE id=?", (rid,)).fetchone()["end_at"]
    clock.t = end - 10 * 60
    assert "최대 2회" in user.post(f"/api/reservations/{rid}/extend").get_json()["error"]["message"]


def test_cancel_and_expire(user, conn, clock):
    rid = reserve(user, 1).get_json()["id"]
    assert user.post(f"/api/reservations/{rid}/return").get_json()["status"] == "cancelled"
    rid = reserve(user, 2, qr_token(conn, 2)).get_json()["id"]
    clock.advance(120 * 60)
    user.get("/api/seats")
    assert conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()["status"] == "expired"


def test_suspended_user_cannot_reserve(user, admin, conn):
    uid = conn.execute("SELECT id FROM users WHERE student_no='20260001'").fetchone()["id"]
    admin.post(f"/api/admin/users/{uid}/suspend", json={"days": 3})
    r = reserve(user, 1)
    assert r.status_code == 403 and err(r) == "SUSPENDED"
    assert user.get("/api/seats").get_json()["me"]["suspended_until"]


def test_seat_page_modes(user, user2, conn):
    tok = qr_token(conn, 1)
    d = user.get(f"/api/seats/1?t={tok}").get_json()
    assert d["page_mode"] == "reserve_now" and d["qr_ok"] is True
    assert user.get("/api/seats/1?t=bad").get_json()["qr_ok"] is False
    d = user.get("/api/seats/5").get_json()
    assert d["page_mode"] == "unavailable" and d["unavailable_note"] == "의자 파손"
    assert user.get("/api/seats/3").get_json()["occupied"] is True
    rid = reserve(user, 1).get_json()["id"]
    assert user.get("/api/seats/1").get_json()["page_mode"] == "mine_checkin"
    assert user2.get("/api/seats/1").get_json()["page_mode"] == "reserved_by_other"
    user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": tok})
    assert user.get("/api/seats/1").get_json()["page_mode"] == "mine_in_use"


def test_call_dedup(user, clock, conn):
    first = user.post("/api/calls", json={"seat_no": 3, "memo": "제 자리에 다른 분이 계세요"})
    assert first.status_code == 201
    clock.advance(30)
    dup = user.post("/api/calls", json={"seat_no": 3})
    assert dup.status_code == 200 and dup.get_json()["duplicate"] is True
    clock.advance(31)
    assert user.post("/api/calls", json={"seat_no": 3}).status_code == 201
