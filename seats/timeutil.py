"""시간 변환. DB는 UTC epoch 초(int), API는 ISO 8601(+09:00), 화면은 KST."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings

# Windows에는 IANA 시간대 DB가 없을 수 있다(tzdata 미설치). 서머타임이 없는 시간대는 고정 오프셋으로 대신한다.
FIXED_OFFSETS = {"Asia/Seoul": 9, "Asia/Tokyo": 9, "UTC": 0, "Etc/UTC": 0}


@lru_cache(maxsize=None)
def tz(name=None):
    name = name or settings.SEATSYNC["TZ"]
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name in FIXED_OFFSETS:
            return timezone(timedelta(hours=FIXED_OFFSETS[name]), name)
        raise RuntimeError(f"시간대 '{name}'를 찾을 수 없습니다. `pip install tzdata` 를 실행하거나 SEATSYNC_TZ 값을 확인하세요.") from None


def to_iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz()).isoformat(timespec="seconds")


def parse_iso(s):
    """ISO 8601 문자열 → epoch 초. 타임존이 없으면 표시용 타임존(KST)으로 간주."""
    if not isinstance(s, str) or not s:
        raise ValueError("시간 형식 오류")
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz())
    return int(dt.timestamp())
