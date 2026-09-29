from conftest import admin_seat, send

import service


def _logs(conn, seat_no):
    return [r["state"] for r in conn.execute("SELECT state FROM status_log WHERE seat_no=? ORDER BY id", (seat_no,))]


def _open_alerts(conn, seat_no=None):
    sql = "SELECT * FROM alerts WHERE resolved_at IS NULL"
    args = ()
    if seat_no is not None:
        sql += " AND seat_no = ?"
        args = (seat_no,)
    return [dict(r) for r in conn.execute(sql, args)]


def _set_setting(conn, key, value):
    conn.execute("UPDATE settings SET value=? WHERE key=?", (str(value), key))


def test_log_only_on_transition(device, clock, conn):
    send(device, clock, {1: ("empty", clock())})
    for _ in range(3):
        clock.advance(5)
        send(device, clock, {1: ("empty", clock() - 5)})
    assert _logs(conn, 1) == ["AVAILABLE"]
    clock.advance(5)
    send(device, clock, {1: ("person", clock())})
    clock.advance(5)
    send(device, clock, {1: ("person", clock() - 5)})
    assert _logs(conn, 1) == ["AVAILABLE", "TEMP_OCCUPIED"]
    row = conn.execute("SELECT prev_state FROM status_log WHERE seat_no=1 ORDER BY id DESC LIMIT 1").fetchone()
    assert row["prev_state"] == "AVAILABLE"


def test_alert_created_once_and_auto_resolved(device, clock, conn):
    start = clock()
    send(device, clock, {1: ("person", start)})
    clock.advance(61)
    send(device, clock, {1: ("person", start)})
    assert [a["type"] for a in _open_alerts(conn, 1)] == ["unauthorized"]
    for _ in range(3):
        clock.advance(5)
        send(device, clock, {1: ("person", start)})
    assert len(_open_alerts(conn, 1)) == 1

    clock.advance(5)
    send(device, clock, {1: ("empty", clock())})
    assert _open_alerts(conn, 1) == []
    row = conn.execute("SELECT * FROM alerts WHERE seat_no=1").fetchone()
    assert row["resolution"] == "auto" and row["resolved_by"] is None


def test_handled_alert_not_recreated_while_state_persists(device, clock, conn, admin):
    send(device, clock, {2: ("item", clock())})
    alerts = admin.get("/api/admin/alerts?open=1").get_json()["alerts"]
    assert [a["type"] for a in alerts] == ["item_only"]
    r = admin.post(f"/api/admin/alerts/{alerts[0]['id']}/resolve")
    assert r.status_code == 200
    clock.advance(5)
    send(device, clock, {2: ("item", clock() - 5)})
    assert admin.get("/api/admin/alerts?open=1").get_json()["alerts"] == []
    row = conn.execute("SELECT resolution, resolved_by FROM alerts").fetchone()
    assert row["resolution"] == "handled" and row["resolved_by"] is not None


def test_offline_does_not_resolve_alert(device, clock, conn, app):
    send(device, clock, {2: ("item", clock())})
    clock.advance(60)
    service.refresh(conn, clock())  # 감지 끊김 → OFFLINE
    assert _logs(conn, 2)[-1] == "OFFLINE"
    assert len(_open_alerts(conn, 2)) == 1


def _reserve_and_checkin(user, conn, seat_no):
    """refresh 테스트용: 예약 API를 거치지 않고 체크인된 예약을 직접 만든다."""
    now = conn.execute("SELECT updated_at FROM detections WHERE seat_no=?", (seat_no,)).fetchone()["updated_at"]
    uid = conn.execute("SELECT id FROM users WHERE student_no='20260001'").fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO reservations(user_id, seat_no, status, start_at, end_at, checked_in_at, source) "
        "VALUES (?,?,'in_use',?,?,?,'seat_page')", (uid, seat_no, now, now + 7200, now))
    return cur.lastrowid


def test_hoarding_alert_and_force_return(device, clock, conn, user, admin):
    send(device, clock, {3: ("person", clock())})
    res_id = _reserve_and_checkin(user, conn, 3)
    clock.advance(10)
    away = clock()
    send(device, clock, {3: ("item", away)})
    assert admin_seat(admin, 3)["state"] == "AWAY_WITH_ITEM"
    clock.advance(30 * 60)
    send(device, clock, {3: ("item", away)})
    seat = admin_seat(admin, 3)
    assert seat["state"] == "HOARDING"
    assert seat["reservation"]["id"] == res_id
    assert [a["type"] for a in _open_alerts(conn, 3)] == ["hoarding"]

    r = admin.post(f"/api/admin/reservations/{res_id}/force-return")
    assert r.status_code == 200
    rs = conn.execute("SELECT status, ended_at FROM reservations WHERE id=?", (res_id,)).fetchone()
    assert rs["status"] == "force_returned" and rs["ended_at"] == clock()
    closed = conn.execute("SELECT resolution FROM alerts WHERE type='hoarding'").fetchone()
    assert closed["resolution"] == "force_returned"
    # 짐이 그대로면 이제 예약 없는 좌석의 무단 물품 점유
    assert admin_seat(admin, 3)["state"] == "ITEM_ONLY"


def test_return_due_alert_only_by_default(device, clock, conn, user, admin):
    send(device, clock, {4: ("person", clock())})
    res_id = _reserve_and_checkin(user, conn, 4)
    clock.advance(5)
    t = clock()
    send(device, clock, {4: ("empty", t)})
    clock.advance(30 * 60)
    send(device, clock, {4: ("empty", t)})
    assert admin_seat(admin, 4)["state"] == "RETURN_DUE"
    assert conn.execute("SELECT status FROM reservations WHERE id=?", (res_id,)).fetchone()["status"] == "in_use"
    assert [a["type"] for a in _open_alerts(conn, 4)] == ["return_due"]


def test_auto_return_empty(device, clock, conn, user, admin):
    _set_setting(conn, "auto_return_empty", 1)
    send(device, clock, {4: ("person", clock())})
    res_id = _reserve_and_checkin(user, conn, 4)
    clock.advance(5)
    t = clock()
    send(device, clock, {4: ("empty", t)})
    clock.advance(30 * 60)
    send(device, clock, {4: ("empty", t)})
    rs = conn.execute("SELECT status, ended_at FROM reservations WHERE id=?", (res_id,)).fetchone()
    assert rs["status"] == "returned" and rs["ended_at"] == clock()
    assert admin_seat(admin, 4)["state"] == "AVAILABLE"
    assert "RETURN_DUE" in _logs(conn, 4)
    assert _open_alerts(conn, 4) == []


def test_settings_change_applies_next_refresh(device, clock, conn, user, admin):
    send(device, clock, {5: ("person", clock())})
    _reserve_and_checkin(user, conn, 5)
    clock.advance(5)
    t = clock()
    send(device, clock, {5: ("item", t)})
    clock.advance(90)
    send(device, clock, {5: ("item", t)})
    assert admin_seat(admin, 5)["state"] == "AWAY_WITH_ITEM"
    r = admin.put("/api/admin/settings", json={"hoarding_min": 1})
    assert r.status_code == 200
    assert admin_seat(admin, 5)["state"] == "HOARDING"


def test_settings_validation(admin, user):
    assert admin.put("/api/admin/settings", json={"nope": 1}).status_code == 400
    assert admin.put("/api/admin/settings", json={"grace_sec": 5}).status_code == 400
    assert admin.put("/api/admin/settings", json={"grace_sec": "abc"}).status_code == 400
    assert admin.put("/api/admin/settings", json={"grace_sec": 20}).get_json()["settings"]["grace_sec"] == 20
    assert user.put("/api/admin/settings", json={"grace_sec": 20}).status_code == 403
    assert user.get("/api/admin/seats").status_code == 403


def test_stats(device, clock, conn, user, admin):
    from datetime import datetime
    from config import tz
    send(device, clock, {1: ("person", clock())})
    _reserve_and_checkin(user, conn, 1)
    clock.advance(600)
    send(device, clock, {1: ("person", clock() - 600)})
    clock.advance(600)
    day = datetime.fromtimestamp(clock(), tz()).date().isoformat()
    data = admin.get(f"/api/admin/stats?date={day}").get_json()
    assert len(data["hours"]) == 24
    assert sum(h["in_use_min"] for h in data["hours"]) > 0
