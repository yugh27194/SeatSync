from conftest import admin_seat, set_state, user_id


def _logs(conn, seat_no):
    return [r["situation"] for r in conn.execute("SELECT situation FROM status_log WHERE seat_no=? ORDER BY id", (seat_no,))]


def _open(conn, seat_no):
    return [r["type"] for r in conn.execute(
        "SELECT type FROM alerts WHERE resolved_at IS NULL AND seat_no=? ORDER BY id", (seat_no,))]


def _in_use(conn, seat_no, now, student_no="20260001"):
    cur = conn.execute(
        "INSERT INTO reservations(user_id, seat_no, status, start_at, end_at, checked_in_at) VALUES (?,?,'in_use',?,?,?)",
        (user_id(conn, student_no), seat_no, now, now + 7200, now))
    return cur.lastrowid


def test_initial_temporary_states(admin):
    """seats.json의 임시 배분: A-3 사용중(예약 없음), B-1 사용불가, 나머지 빈자리."""
    seats = {s["no"]: s for s in admin.get("/api/admin/seats").get_json()["seats"]}
    assert seats[1]["seat_state"] == "available"
    assert seats[3]["seat_state"] == "in_use" and seats[3]["situation"] == "unauthorized"
    assert seats[5]["seat_state"] == "unavailable" and seats[5]["actual_note"] == "의자 파손"
    summary = admin.get("/api/admin/seats").get_json()["summary"]
    assert summary == {"available": 6, "in_use": 1, "unavailable": 1, "issues": 1}


def test_log_only_on_transition(admin, conn):
    for _ in range(3):
        admin.get("/api/admin/seats")
    assert _logs(conn, 1) == ["ok"]
    set_state(admin, 1, "occupied")
    admin.get("/api/admin/seats")
    assert _logs(conn, 1) == ["ok", "unauthorized"]


def test_alert_created_once_and_auto_resolved(admin, conn):
    set_state(admin, 1, "occupied")
    for _ in range(3):
        admin.get("/api/admin/seats")
    assert _open(conn, 1) == ["unauthorized"]
    set_state(admin, 1, "empty")
    assert _open(conn, 1) == []
    row = conn.execute("SELECT resolution, resolved_by FROM alerts WHERE seat_no=1").fetchone()
    assert row["resolution"] == "auto" and row["resolved_by"] is None


def test_handled_alert_not_recreated(admin, conn):
    set_state(admin, 2, "occupied")
    a = admin.get("/api/admin/alerts?open=1").get_json()["alerts"]
    a = [x for x in a if x["seat_no"] == 2][0]
    assert admin.post(f"/api/admin/alerts/{a['id']}/resolve", json={"memo": "퇴실 안내"}).status_code == 200
    admin.get("/api/admin/seats")
    assert _open(conn, 2) == []


def test_away_after_limit(admin, conn, clock):
    set_state(admin, 4, "occupied")
    _in_use(conn, 4, clock())
    set_state(admin, 4, "empty")
    s = admin_seat(admin, 4)
    assert s["situation"] == "ok" and s["note"] == "잠시 자리 비움" and s["deadline_sec"] == 30 * 60
    clock.advance(30 * 60)
    assert admin_seat(admin, 4)["situation"] == "away"
    assert _open(conn, 4) == ["away"]
    set_state(admin, 4, "occupied")  # 돌아옴
    assert admin_seat(admin, 4)["situation"] == "ok" and _open(conn, 4) == []


def test_seat_unavailable_with_reservation(admin, user, conn):
    user.post("/api/reservations", json={"seat_no": 6})
    set_state(admin, 6, "unavailable", note="콘센트 고장")
    s = admin_seat(admin, 6)
    assert s["seat_state"] == "unavailable" and s["situation"] == "seat_unavailable"
    assert _open(conn, 6) == ["seat_unavailable"]


def test_no_show_sweep_alert(user, conn, clock, admin):
    rid = user.post("/api/reservations", json={"seat_no": 1}).get_json()["id"]
    clock.advance(15 * 60 + 1)
    admin.get("/api/admin/seats")
    assert conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()["status"] == "no_show"
    assert _open(conn, 1) == ["no_show"]
