from conftest import admin_seat, qr_token, send


def err(r):
    return r.get_json()["error"]["code"]


def reserve(client, seat_no, token=None):
    body = {"seat_no": seat_no}
    if token:
        body["qr_token"] = token
    return client.post("/api/reservations", json=body)


def test_reserve_success(user, clock):
    r = reserve(user, 1)
    assert r.status_code == 201
    d = r.get_json()
    assert d["status"] == "reserved" and d["seat_label"] == "A-1"
    assert d["checkin_deadline"] is not None and d["can_extend"] is False
    assert d["end_at"].endswith("+09:00")
    seats = user.get("/api/seats").get_json()
    assert next(s for s in seats["seats"] if s["no"] == 1)["view"] == "mine"
    assert seats["my_reservation"]["id"] == d["id"]


def test_user_api_hides_judgement(user, user2, device, clock):
    send(device, clock, {1: ("person", clock() - 600), 2: ("empty", clock())})
    reserve(user2, 2)
    data = user.get("/api/seats").get_json()
    text = str(data)
    for word in ("UNAUTHORIZED", "HOARDING", "state", "사석화", "무단"):
        assert word not in text
    views = {s["no"]: s["view"] for s in data["seats"]}
    assert views[1] == "unavailable" and views[2] == "taken"
    assert views[3] == "unavailable"  # 감지 없음, 예약 없음 → 확인 중


def test_seat_taken_409(user, user2):
    assert reserve(user, 1).status_code == 201
    r = reserve(user2, 1)
    assert r.status_code == 409 and err(r) == "SEAT_TAKEN"


def test_user_duplicate_409(user):
    assert reserve(user, 1).status_code == 201
    r = reserve(user, 2)
    assert r.status_code == 409 and err(r) == "ALREADY_HAS_RESERVATION"


def test_unknown_seat_404(user):
    assert reserve(user, 99).status_code == 404


def test_login_required_401(app):
    r = app.test_client().post("/api/reservations", json={"seat_no": 1})
    assert r.status_code == 401 and err(r) == "UNAUTHENTICATED"


def test_seat_occupied_and_qr_exception(user, device, clock, conn, admin):
    send(device, clock, {1: ("person", clock())})
    r = reserve(user, 1)
    assert r.status_code == 409 and err(r) == "SEAT_OCCUPIED"
    r = reserve(user, 1, "wrong-token")
    assert r.status_code == 409 and err(r) == "SEAT_OCCUPIED"
    r = reserve(user, 1, qr_token(conn, 1))
    assert r.status_code == 201
    d = r.get_json()
    assert d["status"] == "in_use" and d["checked_in_at"] is not None
    row = conn.execute("SELECT source FROM reservations WHERE id=?", (d["id"],)).fetchone()
    assert row["source"] == "seat_page"
    assert admin_seat(admin, 1)["state"] == "IN_USE"


def test_item_only_seat_occupied(user, device, clock):
    send(device, clock, {2: ("item", clock())})
    assert err(reserve(user, 2)) == "SEAT_OCCUPIED"


def test_wrong_token_on_free_seat_403(user):
    r = reserve(user, 1, "bad")
    assert r.status_code == 403 and err(r) == "BAD_QR_TOKEN"


def test_checkin(user, user2, conn, clock):
    rid = reserve(user, 1).get_json()["id"]
    r = user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": "bad"})
    assert r.status_code == 403 and err(r) == "BAD_QR_TOKEN"
    r = user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 2)})
    assert r.status_code == 403  # 다른 좌석 토큰
    assert user2.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)}).status_code == 404
    clock.advance(60)
    r = user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)})
    assert r.status_code == 200
    assert r.get_json()["status"] == "in_use"
    row = conn.execute("SELECT checked_in_at FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["checked_in_at"] == clock()
    r = user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)})
    assert r.status_code == 409 and err(r) == "INVALID_STATE"


def _in_use(user, conn, seat_no=1):
    return reserve(user, seat_no, qr_token(conn, seat_no)).get_json()["id"]


def test_extend_window(user, conn, clock):
    rid = _in_use(user, conn)
    # 기본 120분 이용, 연장 창 30분
    r = user.post(f"/api/reservations/{rid}/extend")
    assert r.status_code == 409 and err(r) == "EXTEND_NOT_ALLOWED"
    assert "30분" in r.get_json()["error"]["message"]

    clock.advance(90 * 60 - 1)  # 남은 30분 1초 → 아직 불가
    assert user.post(f"/api/reservations/{rid}/extend").status_code == 409
    clock.advance(1)            # 남은 정확히 30분 → 가능
    r = user.post(f"/api/reservations/{rid}/extend")
    assert r.status_code == 200
    d = r.get_json()
    assert d["extend_count"] == 1 and d["remaining_sec"] == 90 * 60


def test_extend_limit(user, conn, clock):
    rid = _in_use(user, conn)
    for _ in range(2):
        row = conn.execute("SELECT end_at FROM reservations WHERE id=?", (rid,)).fetchone()
        clock.t = row["end_at"] - 10 * 60
        assert user.post(f"/api/reservations/{rid}/extend").status_code == 200
    row = conn.execute("SELECT end_at FROM reservations WHERE id=?", (rid,)).fetchone()
    clock.t = row["end_at"] - 10 * 60
    r = user.post(f"/api/reservations/{rid}/extend")
    assert r.status_code == 409 and "최대 2회" in r.get_json()["error"]["message"]


def test_extend_requires_in_use(user):
    rid = reserve(user, 1).get_json()["id"]
    assert err(user.post(f"/api/reservations/{rid}/extend")) == "EXTEND_NOT_ALLOWED"


def test_return(user, conn):
    rid = reserve(user, 1).get_json()["id"]
    r = user.post(f"/api/reservations/{rid}/return")
    assert r.get_json()["status"] == "cancelled"
    rid = _in_use(user, conn, 2)
    r = user.post(f"/api/reservations/{rid}/return")
    assert r.status_code == 200 and r.get_json()["status"] == "returned"
    assert conn.execute("SELECT ended_at FROM reservations WHERE id=?", (rid,)).fetchone()["ended_at"]
    r = user.post(f"/api/reservations/{rid}/return")
    assert r.status_code == 409 and err(r) == "INVALID_STATE"
    # 반납 후 새 예약 가능
    assert reserve(user, 3).status_code == 201


def test_no_show_sweep(user, conn, clock):
    rid = reserve(user, 1).get_json()["id"]
    clock.advance(15 * 60)
    user.get("/api/seats")
    assert conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()["status"] == "reserved"
    clock.advance(1)
    data = user.get("/api/seats").get_json()
    row = conn.execute("SELECT status, ended_at FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "no_show" and row["ended_at"] == clock()
    assert data["my_reservation"] is None
    a = conn.execute("SELECT type, reservation_id FROM alerts").fetchone()
    assert a["type"] == "no_show" and a["reservation_id"] == rid
    # 늦은 체크인 불가
    r = user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": qr_token(conn, 1)})
    assert r.status_code == 409


def test_expired_sweep(user, conn, clock):
    rid = _in_use(user, conn)
    clock.advance(120 * 60 - 1)
    user.get("/api/seats")
    assert conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()["status"] == "in_use"
    clock.advance(1)
    user.get("/api/seats")
    row = conn.execute("SELECT status, ended_at FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "expired" and row["ended_at"] == clock()


def test_seat_page_modes(user, user2, conn, device, clock):
    tok = qr_token(conn, 1)
    d = user.get(f"/api/seats/1?t={tok}").get_json()
    assert d["page_mode"] == "reserve_now" and d["qr_ok"] is True and d["offline"] is True
    assert user.get("/api/seats/1?t=bad").get_json()["qr_ok"] is False
    rid = reserve(user, 1).get_json()["id"]
    assert user.get("/api/seats/1").get_json()["page_mode"] == "mine_checkin"
    assert user2.get("/api/seats/1").get_json()["page_mode"] == "reserved_by_other"
    user.post(f"/api/reservations/{rid}/checkin", json={"qr_token": tok})
    send(device, clock, {1: ("person", clock())})
    d = user.get("/api/seats/1").get_json()
    assert d["page_mode"] == "mine_in_use" and d["offline"] is False
    # 다른 좌석 페이지에서 내 예약 정보 확인 가능
    d = user.get("/api/seats/2").get_json()
    assert d["page_mode"] == "reserve_now" and d["my_reservation"]["seat_label"] == "A-1"


def test_call_dedup(user, clock, conn):
    r = user.post("/api/calls", json={"seat_no": 3, "memo": "제 예약석에 다른 분이 앉아 계세요"})
    assert r.status_code == 201
    first = r.get_json()["alert"]["id"]
    clock.advance(30)
    r = user.post("/api/calls", json={"seat_no": 3})
    assert r.status_code == 200 and r.get_json()["duplicate"] is True and r.get_json()["alert"]["id"] == first
    clock.advance(31)
    assert user.post("/api/calls", json={"seat_no": 3}).status_code == 201
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE type='call'").fetchone()[0] == 2
