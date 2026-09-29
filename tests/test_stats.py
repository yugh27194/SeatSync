from datetime import datetime

from config import tz
from routes.api_admin import compute_hourly_stats


def _ts(h, m=0, day=30):
    return int(datetime(2026, 9, day, h, m, tzinfo=tz()).timestamp())


def _log(conn, seat_no, state, at):
    conn.execute("INSERT INTO status_log(seat_no, state, at) VALUES (?,?,?)", (seat_no, state, at))


def test_hourly_split_and_rate(conn):
    _log(conn, 1, "IN_USE", _ts(14, 0))
    _log(conn, 1, "HOARDING", _ts(14, 30))
    _log(conn, 1, "AVAILABLE", _ts(15, 15))
    # 전날 밤부터 이어진 상태도 당일 0시부터 센다
    _log(conn, 2, "UNAUTHORIZED", _ts(23, 0, day=29))
    _log(conn, 2, "AVAILABLE", _ts(1, 0))
    hours = compute_hourly_stats(conn, _ts(0), _ts(0) + 86400, _ts(0) + 86400)
    h = {x["hour"]: x for x in hours}
    assert h[14]["in_use_min"] == 30 and h[14]["hoarding_min"] == 30 and h[14]["hoarding_rate"] == 0.5
    assert h[15]["hoarding_min"] == 15 and h[15]["hoarding_rate"] == 1.0
    assert h[0]["unauthorized_min"] == 60 and h[1]["unauthorized_min"] == 0
    assert h[16]["in_use_min"] == 0 and h[16]["hoarding_rate"] == 0.0


def test_stats_capped_at_now(conn):
    _log(conn, 1, "IN_USE", _ts(10, 0))
    hours = compute_hourly_stats(conn, _ts(0), _ts(0) + 86400, _ts(10, 20))
    assert hours[10]["in_use_min"] == 20
    assert hours[11]["in_use_min"] == 0


def test_stats_bad_date(admin):
    assert admin.get("/api/admin/stats?date=2026-13-01").status_code == 400
    assert admin.get("/api/admin/stats?date=2026-09-30").get_json()["date"] == "2026-09-30"
