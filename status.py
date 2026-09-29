"""좌석 상태 판정과 예약 대조. 순수 함수만 둔다 — DB·Flask·현재 시각에 의존하지 않는다.

좌석 상태는 세 가지뿐이다: 빈자리 / 예약(사용중) / 사용불가.
여기에 예약 기록과 현장 상태를 대조한 '상황'(정상 또는 확인 필요)을 덧붙여 관리자가 조치한다.
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
}

# key: (라벨, 단위, 설명, 최소, 최대)
SETTINGS_META = {
    "checkin_limit_min": ("체크인 제한", "분", "예약 후 이 시간 안에 체크인하지 않으면 미입실로 자동 취소", 1, 120),
    "away_limit_min": ("이탈 기준", "분", "이용 중 좌석이 이 시간 넘게 완전히 비어 있으면 '이탈'로 표시", 1, 240),
    "hoarding_min": ("사석화 기준", "분", "이용 중 좌석에 짐만 두고 이 시간 넘게 자리를 비우면 '사석화'로 표시", 1, 240),
    "default_use_min": ("기본 이용 시간", "분", "예약 1회 이용 시간", 10, 720),
    "extend_min": ("연장 시간", "분", "연장 1회당 늘어나는 시간", 10, 360),
    "extend_window_min": ("연장 가능 시점", "분", "남은 시간이 이 값 이하일 때만 연장 가능", 1, 240),
    "max_extends": ("최대 연장 횟수", "회", "예약 1건당 이용자가 직접 연장할 수 있는 횟수", 0, 10),
    "warning_limit": ("정지 권장 경고 수", "회", "경고가 이 횟수 이상 쌓이면 관리자 화면에 이용 정지를 권장", 1, 20),
    "suspend_days": ("기본 정지 기간", "일", "이용 정지 시 기본으로 제안하는 기간", 1, 90),
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

    @classmethod
    def from_dict(cls, d):
        merged = {**DEFAULT_SETTINGS, **{k: v for k, v in (d or {}).items() if k in DEFAULT_SETTINGS}}
        return cls(**{f.name: int(merged[f.name]) for f in fields(cls)})

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


# ---------------------------------------------------------------- 좌석 상태 (3가지)

AVAILABLE, IN_USE, UNAVAILABLE = "available", "in_use", "unavailable"
SEAT_STATES = {AVAILABLE: "빈자리", IN_USE: "예약(사용중)", UNAVAILABLE: "사용불가"}

# 현장 상태 (관리자가 임시 배분하거나 카메라가 보고). '짐만 있음'은 사석화·짐으로 자리 맡기 판단용.
ACTUAL_STATES = {"empty": "빈자리", "occupied": "사용중", "item": "짐만 있음", "unavailable": "사용불가"}

# ---------------------------------------------------------------- 대조 결과(상황)

OK = "ok"
SITUATIONS = {
    OK:                 {"label": "정상",             "desc": ""},
    "unauthorized":     {"label": "무단 점유",        "desc": "예약 없이 좌석을 사용하거나 짐으로 자리를 맡아 두었습니다."},
    "away":             {"label": "이탈",             "desc": "이용 중인 좌석이 기준 시간보다 오래 비어 있습니다."},
    "hoarding":         {"label": "사석화",           "desc": "이용 중인 좌석에 짐만 두고 기준 시간보다 오래 자리를 비웠습니다."},
    "no_checkin":       {"label": "체크인 누락",      "desc": "예약 좌석에 착석(또는 짐)이 있지만 체크인하지 않았습니다."},
    "seat_unavailable": {"label": "예약 좌석 사용불가", "desc": "예약된 좌석이 사용불가 상태입니다. 다른 좌석으로 옮겨 주세요."},
}
ISSUES = [k for k in SITUATIONS if k != OK]

ALERT_TYPE_LABELS = {**{k: v["label"] for k, v in SITUATIONS.items() if k != OK},
                     "no_show": "미입실", "call": "이용자 호출"}


@dataclass(frozen=True)
class Reservation:
    id: int
    status: str
    start_at: int
    end_at: int
    checked_in_at: int | None


@dataclass(frozen=True)
class Actual:
    state: str          # empty | occupied | item | unavailable
    since: int


@dataclass(frozen=True)
class Judgement:
    seat_state: str     # available | in_use | unavailable
    situation: str      # ok | unauthorized | away | hoarding | no_checkin | seat_unavailable
    since: int          # 현재 상황이 시작된 시각
    deadline: int | None = None  # 다음 변화 예정 시각 (체크인 마감, 자리 비움 허용 종료)
    note: str | None = None      # 정상일 때의 보조 설명 (입실 대기, 잠시 자리 비움)


def judge(res: Reservation | None, actual: Actual, now: int, s: Settings) -> Judgement:
    """res: 해당 좌석의 활성 예약(reserved/in_use)만. actual: 현장 상태."""
    a = actual.state
    since = min(actual.since, now)

    if a == "unavailable":
        if res is not None:
            return Judgement(UNAVAILABLE, "seat_unavailable", max(since, res.start_at))
        return Judgement(UNAVAILABLE, OK, since)

    if res is None:
        if a in ("occupied", "item"):
            return Judgement(IN_USE, "unauthorized", since)
        return Judgement(AVAILABLE, OK, since)

    if res.status == "reserved":
        checkin_deadline = res.start_at + s.checkin_limit_min * 60
        start = max(since, res.start_at)
        if a in ("occupied", "item"):
            return Judgement(IN_USE, "no_checkin", start, checkin_deadline)
        return Judgement(IN_USE, OK, res.start_at, checkin_deadline, "입실 대기")

    # in_use: 자리 비움은 체크인 시점 이후부터 센다
    start = max(since, res.checked_in_at or res.start_at)
    if a == "occupied":
        return Judgement(IN_USE, OK, start)
    if a == "item":
        limit, note, issue = s.hoarding_min * 60, "짐만 두고 자리 비움", "hoarding"
    else:
        limit, note, issue = s.away_limit_min * 60, "잠시 자리 비움", "away"
    if now - start < limit:
        return Judgement(IN_USE, OK, start, start + limit, note)
    return Judgement(IN_USE, issue, start + limit)


def user_view(seat_state):
    """사용자 지도 분류. 대조 결과(미예약 사용 등)는 사용자에게 노출하지 않는다."""
    return {AVAILABLE: "available", IN_USE: "taken", UNAVAILABLE: "unavailable"}[seat_state]
