"""Windows처럼 시간대 DB가 없는 환경에서도 동작해야 한다."""
from zoneinfo import ZoneInfoNotFoundError

import pytest

import config
from timeutil import parse_iso, to_iso


@pytest.fixture
def no_tzdb(monkeypatch):
    def fail(key):
        raise ZoneInfoNotFoundError(f"No time zone found with key {key}")
    monkeypatch.setattr(config, "ZoneInfo", fail)
    config.tz.cache_clear()
    yield
    config.tz.cache_clear()


def test_seoul_falls_back_to_fixed_offset(no_tzdb):
    assert to_iso(1_790_000_000) == "2026-09-21T23:13:20+09:00"
    assert parse_iso("2026-09-21T23:13:20") == 1_790_000_000


def test_unknown_zone_gives_clear_error(no_tzdb):
    with pytest.raises(RuntimeError, match="tzdata"):
        config.tz("America/New_York")


def test_pages_work_without_tzdb(no_tzdb, user, admin):
    assert user.get("/api/seats").status_code == 200
    assert admin.get("/api/admin/seats").status_code == 200
    assert admin.get("/api/admin/stats").status_code == 200
