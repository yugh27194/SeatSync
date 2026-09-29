from datetime import datetime

from conftest import ApiClient

from seats.models import StatusLog
from seats.services import compute_hourly_stats
from seats.timeutil import tz


def _ts(h, m=0, day=30):
    return int(datetime(2026, 9, day, h, m, tzinfo=tz()).timestamp())


def _log(no, st, detail, at):
    StatusLog.objects.create(seat_no=no, seat_state=st, detail=detail, at=at)


def test_hourly_split_and_rate():
    _log(1, "in_use", "using", _ts(14, 0))
    _log(1, "in_use", "away", _ts(14, 30))
    _log(1, "available", "empty", _ts(15, 15))
    _log(3, "in_use", "hoarding", _ts(16, 0))
    _log(3, "available", "empty", _ts(16, 30))
    _log(2, "in_use", "unauthorized", _ts(23, 0, day=29))  # 전날부터 이어짐
    _log(2, "available", "empty", _ts(1, 0))
    h = {x["hour"]: x for x in compute_hourly_stats(_ts(0), _ts(0) + 86400, _ts(0) + 86400)}
    assert h[14]["in_use_min"] == 30 and h[14]["away_min"] == 30 and h[14]["issue_rate"] == 0.5
    assert h[15]["away_min"] == 15 and h[15]["issue_rate"] == 1.0
    assert h[16]["hoarding_min"] == 30
    assert h[0]["unauthorized_min"] == 60 and h[1]["unauthorized_min"] == 0


def test_item_counts_as_normal_use():
    _log(1, "in_use", "item", _ts(10, 0))
    hours = compute_hourly_stats(_ts(0), _ts(0) + 86400, _ts(10, 20))
    assert hours[10]["in_use_min"] == 20 and hours[11]["in_use_min"] == 0


def test_stats_bad_date(admin):
    assert admin.get("/api/admin/stats?date=2026-13-01").status_code == 400
    assert admin.jget("/api/admin/stats?date=2026-09-30")["date"] == "2026-09-30"


def test_timezone_fallback_without_tzdb(monkeypatch):
    """Windows처럼 시간대 DB가 없는 환경에서도 Asia/Seoul은 +09:00으로 동작."""
    from zoneinfo import ZoneInfoNotFoundError

    from seats import timeutil

    def fail(key):
        raise ZoneInfoNotFoundError(key)
    monkeypatch.setattr(timeutil, "ZoneInfo", fail)
    timeutil.tz.cache_clear()
    try:
        assert timeutil.to_iso(1_790_000_000) == "2026-09-21T23:13:20+09:00"
        assert ApiClient().get("/login").status_code == 200
    finally:
        timeutil.tz.cache_clear()
