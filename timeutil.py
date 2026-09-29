"""시간 변환 유틸. DB는 UTC epoch 초(int), API는 ISO 8601(+09:00)."""
from datetime import datetime

from config import tz


def to_iso(epoch, tz_name=None):
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz(tz_name)).isoformat(timespec="seconds")


def parse_iso(s, tz_name=None):
    """ISO 8601 문자열 → epoch 초. 타임존이 없으면 표시용 타임존(KST)으로 간주."""
    if not isinstance(s, str) or not s:
        raise ValueError("시간 형식 오류")
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # SPEC-ASSUMPTION: 타임존 없는 시각은 SEATSYNC_TZ 기준으로 해석한다.
        dt = dt.replace(tzinfo=tz(tz_name))
    return int(dt.timestamp())
