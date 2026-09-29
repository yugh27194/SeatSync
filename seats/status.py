"""좌석 상태 판정과 예약 대조. 순수 함수만 둔다 — DB·Django·현재 시각에 의존하지 않는다.

좌석 상태는 세 가지(빈자리 / 사용중 / 사용불가)이고, 각각 세부 상태를 가진다.
세부 상태 중 일부(무단 점유·이탈·사석화 등)는 '처리 필요'로 관리자 조치 대상이다.
"""
from dataclasses import dataclass, fields

# ---------------------------------------------------------------- 설정값

DEFAULT_SETTINGS = {
    "checkin_limit_min": 15,
    "away_limit_min": 30,
    "hoarding_min": 30,
    "default_use_min": 120,
    "extend_min": 60,
    "extend_window_min": 30,
    "max_extends": 2,
    "warning_limit": 3,
    "suspend_days": 3,
    "prewarn_min": 10,
    "waitlist_hold_min": 5,
    "open_hour": 6,
    "close_hour": 24,
}

# key: (라벨, 단위, 설명, 최소, 최대)
SETTINGS_META = {
    "checkin_limit_min": ("체크인 제한", "분", "예약 후 이 시간 안에 체크인하지 않으면 미입실로 자동 취소", 1, 120),
    "away_limit_min": ("이탈 기준", "분", "이용 중 좌석이 이 시간 넘게 완전히 비어 있으면 '이탈'", 1, 240),
    "hoarding_min": ("사석화 기준", "분", "이용 중 좌석에 짐만 두고 이 시간 넘게 자리를 비우면 '사석화'", 1, 240),
    "default_use_min": ("기본 이용 시간", "분", "예약 1회 이용 시간", 10, 720),
    "extend_min": ("연장 시간", "분", "연장 1회당 늘어나는 시간", 10, 360),
    "extend_window_min": ("연장 가능 시점", "분", "남은 시간이 이 값 이하일 때만 연장 가능", 1, 240),
    "max_extends": ("최대 연장 횟수", "회", "예약 1건당 이용자가 직접 연장할 수 있는 횟수", 0, 10),
    "warning_limit": ("정지 권장 경고 수", "회", "경고가 이 횟수 이상 쌓이면 이용 정지를 권장", 1, 20),
    "suspend_days": ("기본 정지 기간", "일", "이용 정지 시 기본으로 제안하는 기간", 1, 90),
    "prewarn_min": ("사전 경고 시점", "분", "이탈·사석화·체크인 마감 기준 시간 이 분 전에 본인에게 사전 경고", 1, 60),
    "waitlist_hold_min": ("빈자리 안내 유지", "분", "빈자리 알림 대기자에게 먼저 예약할 기회를 주는 시간", 1, 30),
    "open_hour": ("운영 시작", "시", "혼잡도 통계에 쓰는 운영 시작 시각", 0, 23),
    "close_hour": ("운영 종료", "시", "혼잡도 통계에 쓰는 운영 종료 시각 (24 = 자정)", 1, 24),
}


@dataclass(frozen=True)
class Settings:
    checkin_limit_min: int
    away_limit_min: int
    hoarding_min: int
    default_use_min: int
    extend_min: int
    extend_window_min: int
    max_extends: int
    warning_limit: int
    suspend_days: int
    prewarn_min: int
    waitlist_hold_min: int
    open_hour: int
    close_hour: int

    @classmethod
    def from_dict(cls, d):
        merged = {**DEFAULT_SETTINGS, **{k: v for k, v in (d or {}).items() if k in DEFAULT_SETTINGS}}
        return cls(**{f.name: int(merged[f.name]) for f in fields(cls)})

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


# ---------------------------------------------------------------- 좌석 상태 3가지 + 세부 상태

AVAILABLE, IN_USE, UNAVAILABLE = "available", "in_use", "unavailable"
SEAT_STATES = {AVAILABLE: "빈자리", IN_USE: "사용중", UNAVAILABLE: "사용불가"}

OK = "ok"

# 세부 상태: (상위 상태, 라벨, 처리 필요 여부, 설명)
DETAILS = {
    "empty":            (AVAILABLE,   "빈자리",           False, ""),
    "using":            (IN_USE,      "이용 중",          False, ""),
    "waiting":          (IN_USE,      "입실 대기",        False, "예약 후 체크인 전입니다."),
    "seated_unchecked": (IN_USE,      "착석(체크인 전)",  False, "예약자가 착석했지만 아직 체크인하지 않았습니다."),
    "away_short":       (IN_USE,      "잠시 자리 비움",   False, ""),
    "item":             (IN_USE,      "짐만 있음",        False, ""),
    "unauthorized":     (IN_USE,      "무단 점유",        True,  "예약 없이 좌석을 사용하거나 짐으로 자리를 맡아 두었습니다."),
    "no_checkin":       (IN_USE,      "체크인 누락",      True,  "예약 좌석에 착석(또는 짐)이 있지만 체크인하지 않았습니다."),
    "away":             (IN_USE,      "이탈",             True,  "이용 중인 좌석이 기준 시간보다 오래 비어 있습니다."),
    "hoarding":         (IN_USE,      "사석화",           True,  "짐만 두고 기준 시간보다 오래 자리를 비웠습니다."),
    "broken":           (UNAVAILABLE, "고장",             False, ""),
    "maintenance":      (UNAVAILABLE, "점검·청소",        False, ""),
    "blocked":          (UNAVAILABLE, "사용 중지",        False, ""),
    "seat_unavailable": (UNAVAILABLE, "예약 좌석 사용불가", True, "예약된 좌석이 사용불가 상태입니다. 다른 좌석으로 옮겨 주세요."),
}
ISSUES = [k for k, v in DETAILS.items() if v[2]]
UNAVAILABLE_REASONS = ("broken", "maintenance", "blocked")

ALERT_TYPE_LABELS = {**{k: DETAILS[k][1] for k in ISSUES}, "no_show": "미입실", "call": "이용자 호출"}

# 관리자가 좌석에 직접 부여할 수 있는 세부 상태 → (현장 상태, 표시, 사용불가 사유, 조건)
#   mark 'ok'    : 관리자가 확인한 정상 이용 — 예약이 없어도 '처리 필요'로 빠지지 않는다
#   mark 'issue' : 관리자가 문제로 지정 — 기준 시간을 기다리지 않고 바로 '처리 필요'
#   조건 'in_use': 이용 중인 예약이 있는 좌석에만 부여 가능
ASSIGNABLE = {
    "empty":        ("empty",       None,    None,          None),
    "using":        ("occupied",    "ok",    None,          None),
    "item":         ("item",        "ok",    None,          None),
    "unauthorized": ("occupied",    "issue", None,          None),
    "away":         ("empty",       "issue", None,          "in_use"),
    "hoarding":     ("item",        "issue", None,          "in_use"),
    "broken":       ("unavailable", None,    "broken",      None),
    "maintenance":  ("unavailable", None,    "maintenance", None),
    "blocked":      ("unavailable", None,    "blocked",     None),
}
ASSIGN_GROUPS = [
    (AVAILABLE, ["empty"]),
    (IN_USE, ["using", "item", "unauthorized", "away", "hoarding"]),
    (UNAVAILABLE, ["broken", "maintenance", "blocked"]),
]

# 현장 상태 (관리자가 임시 배분하거나 카메라가 보고)
ACTUAL_STATES = {"empty": "비어 있음", "occupied": "사람 있음", "item": "짐만 있음", "unavailable": "사용불가"}


@dataclass(frozen=True)
class Reservation:
    id: int
    status: str
    start_at: int
    end_at: int
    checked_in_at: int | None


@dataclass(frozen=True)
class Actual:
    state: str                # empty | occupied | item | unavailable
    since: int
    mark: str | None = None   # None(자동) | ok | issue
    reason: str | None = None  # 사용불가 사유: broken | maintenance | blocked
    stale_since: int | None = None  # 카메라 감지를 믿을 수 없게 된 시각(UNKNOWN·유효 시간 경과). 카메라 판정 좌석만


@dataclass(frozen=True)
class Judgement:
    seat_state: str           # available | in_use | unavailable
    detail: str               # DETAILS 키
    since: int                # 현재 세부 상태가 시작된 시각
    deadline: int | None = None  # 다음 변화 예정 시각 (체크인 마감, 이탈·사석화 기준)
    stale: bool = False          # 카메라 감지 확인 불가 — 마지막으로 확인된 상태를 보여 주는 중

    @property
    def situation(self):
        """처리 필요 유형(= detail) 또는 'ok'."""
        return self.detail if DETAILS[self.detail][2] else OK

    @property
    def needs_action(self):
        return DETAILS[self.detail][2]


def _j(detail, since, deadline=None, stale=False):
    return Judgement(DETAILS[detail][0], detail, since, deadline, stale)


def judge(res: Reservation | None, actual: Actual, now: int, s: Settings) -> Judgement:
    """res: 해당 좌석의 활성 예약(reserved/in_use)만. actual: 현장 상태."""
    j = _judge(res, actual, now, s)
    return Judgement(j.seat_state, j.detail, j.since, j.deadline, True) if actual.stale_since is not None else j


def _judge(res, actual, now, s):
    a, mark = actual.state, actual.mark
    since = min(actual.since, now)

    if a == "unavailable":
        if res is not None:
            return _j("seat_unavailable", max(since, res.start_at))
        return _j(actual.reason if actual.reason in UNAVAILABLE_REASONS else "blocked", since)

    if res is None:
        if a in ("occupied", "item"):
            if mark == "ok":  # 관리자가 확인한 이용(현장 허가 등)
                return _j("using" if a == "occupied" else "item", since)
            return _j("unauthorized", since)
        return _j("empty", since)

    if res.status == "reserved":
        checkin_deadline = res.start_at + s.checkin_limit_min * 60
        start = max(since, res.start_at)
        if a in ("occupied", "item"):
            if mark == "ok":
                return _j("seated_unchecked", start, checkin_deadline)
            return _j("no_checkin", start, checkin_deadline)
        return _j("waiting", res.start_at, checkin_deadline)

    # in_use: 자리 비움은 체크인 시점 이후부터 센다
    start = max(since, res.checked_in_at or res.start_at)
    if a == "occupied":
        return _j("unauthorized" if mark == "issue" else "using", start)
    if a == "item":
        limit, short, issue = s.hoarding_min * 60, "item", "hoarding"
    else:
        limit, short, issue = s.away_limit_min * 60, "away_short", "away"
    if mark == "issue":
        return _j(issue, start)
    # 카메라 감지를 믿을 수 없는 동안에는 이탈·사석화로 넘기지 않는다(감지 끊김을 자리 비움으로 오해하지 않게).
    # 끊기기 전에 이미 기준 시간을 넘었다면 그대로 처리 필요.
    if actual.stale_since is not None and actual.stale_since < start + limit:
        return _j(short, start)
    if now - start < limit:
        return _j(short, start, start + limit)
    return _j(issue, start + limit)


def user_view(seat_state):
    """사용자 지도 분류. 세부 상태·처리 필요 여부는 일반 사용자에게 보이지 않는다."""
    return {AVAILABLE: "available", IN_USE: "taken", UNAVAILABLE: "unavailable"}[seat_state]
