"""환경변수 로드 (§12)."""
import os
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    SECRET_KEY = os.environ.get("SEATSYNC_SECRET_KEY", "dev-secret-change-me")
    DEVICE_KEY = os.environ.get("SEATSYNC_DEVICE_KEY", "dev-key")
    DATABASE = os.environ.get("SEATSYNC_DB", os.path.join(BASE_DIR, "seatsync.db"))
    TZ_NAME = os.environ.get("SEATSYNC_TZ", "Asia/Seoul")
    SEATS_FILE = os.path.join(BASE_DIR, "config", "seats.json")
    JSON_AS_ASCII = False


def tz(name=None):
    return ZoneInfo(name or Config.TZ_NAME)
