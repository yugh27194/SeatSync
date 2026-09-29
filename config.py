"""환경변수 로드 (§12)."""
import os
from datetime import timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    SECRET_KEY = os.environ.get("SEATSYNC_SECRET_KEY", "dev-secret-change-me")
    DEVICE_KEY = os.environ.get("SEATSYNC_DEVICE_KEY", "dev-key")
    DATABASE = os.environ.get("SEATSYNC_DB", os.path.join(BASE_DIR, "seatsync.db"))
    TZ_NAME = os.environ.get("SEATSYNC_TZ", "Asia/Seoul")
    SEATS_FILE = os.path.join(BASE_DIR, "config", "seats.json")
    JSON_AS_ASCII = False


# Windows에는 IANA 시간대 DB가 없어 ZoneInfo("Asia/Seoul")가 실패할 수 있다(tzdata 패키지 미설치 시).
# 서머타임이 없는 시간대는 고정 오프셋으로 대신한다.
FIXED_OFFSETS = {"Asia/Seoul": 9, "Asia/Tokyo": 9, "UTC": 0, "Etc/UTC": 0}


@lru_cache(maxsize=None)
def tz(name=None):
    name = name or Config.TZ_NAME
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name in FIXED_OFFSETS:
            return timezone(timedelta(hours=FIXED_OFFSETS[name]), name)
        raise RuntimeError(
            f"시간대 '{name}'를 찾을 수 없습니다. `pip install tzdata` 를 실행하거나 SEATSYNC_TZ 값을 확인하세요."
        ) from None
