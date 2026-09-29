"""좌석 상태 판정 (§5). 순수 함수만 둔다 — DB·Flask·현재 시각에 의존하지 않는다."""
from dataclasses import dataclass, fields

# ---------------------------------------------------------------- 설정값 (§3.1)

DEFAULT_SETTINGS = {
    "grace_sec": 60,
    "checkin_limit_min": 15,
    "hoarding_min": 30,
    "empty_return_min": 30,
    "default_use_min": 120,
    "extend_min": 60,
    "extend_window_min": 30,
    "max_extends": 2,
    "stale_sec": 30,
    "auto_return_empty": 0,
}

# key: (라벨, 단위, 설명, 최소, 최대)
SETTINGS_META = {
    "grace_sec": ("무단 사용 유예", "초", "미예약 좌석에 앉은 뒤 무단 사용으로 보기까지의 유예 시간(임시 점유)", 10, 600),
    "checkin_limit_min": ("체크인 제한", "분", "예약 후 이 시간 안에 체크인하지 않으면 미입실(no_show)로 자동 취소", 1, 120),
    "hoarding_min": ("사석화 기준", "분", "체크인한 좌석에 짐만 있는 상태가 이 시간 지속되면 사석화", 1, 240),
    "empty_return_min": ("반납 대상 기준", "분", "체크인한 좌석이 완전히 빈 상태로 이 시간 지속되면 반납 대상", 1, 240),
    "default_use_min": ("기본 이용 시간", "분", "예약 1회 이용 시간", 10, 720),
    "extend_min": ("연장 시간", "분", "연장 1회당 늘어나는 시간", 10, 360),
    "extend_window_min": ("연장 가능 시점", "분", "남은 시간이 이 값 이하일 때만 연장 가능", 1, 240),
    "max_extends": ("최대 연장 횟수", "회", "예약 1건당 최대 연장 횟수", 0, 10),
    "stale_sec": ("감지 끊김 기준", "초", "카메라 감지 수신이 이 시간 동안 없으면 '감지 끊김'", 10, 600),
    "auto_return_empty": ("반납 대상 자동 반납", "0/1", "1이면 반납 대상 도달 시 자동 반납, 0이면 알림만", 0, 1),
}


@dataclass(frozen=True)
class Settings:
    grace_sec: int
    checkin_limit_min: int
    hoarding_min: int
    empty_return_min: int
    default_use_min: int
    extend_min: int
    extend_window_min: int
    max_extends: int
    stale_sec: int
    auto_return_empty: int

    @classmethod
    def from_dict(cls, d):
        merged = {**DEFAULT_SETTINGS, **(d or {})}
        return cls(**{f.name: int(merged[f.name]) for f in fields(cls)})

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


# ---------------------------------------------------------------- 상태 enum (§5.1)

AVAILABLE = "AVAILABLE"
TEMP_OCCUPIED = "TEMP_OCCUPIED"
UNAUTHORIZED = "UNAUTHORIZED"
ITEM_ONLY = "ITEM_ONLY"
RESERVED = "RESERVED"
AWAITING_CHECKIN = "AWAITING_CHECKIN"
IN_USE = "IN_USE"
AWAY_WITH_ITEM = "AWAY_WITH_ITEM"
HOARDING = "HOARDING"
AWAY_EMPTY = "AWAY_EMPTY"
RETURN_DUE = "RETURN_DUE"
OFFLINE = "OFFLINE"

# state: 라벨, 관리자 색, 사용자 지도 분류(None = OFFLINE 특수 규칙), 알림 유형
STATE_META = {
    AVAILABLE:        {"label": "빈자리",            "color": "초록",       "view": "available",   "alert": None},
    TEMP_OCCUPIED:    {"label": "임시 점유",         "color": "연노랑",     "view": "unavailable", "alert": None},
    UNAUTHORIZED:     {"label": "무단 사용",         "color": "빨강",       "view": "unavailable", "alert": "unauthorized"},
    ITEM_ONLY:        {"label": "무단 물품 점유",    "color": "주황",       "view": "unavailable", "alert": "item_only"},
    RESERVED:         {"label": "예약됨(입실 전)",   "color": "연파랑",     "view": "taken",       "alert": None},
    AWAITING_CHECKIN: {"label": "체크인 대기(착석함)", "color": "연파랑+점선", "view": "taken",     "alert": None},
    IN_USE:           {"label": "정상 이용",         "color": "파랑",       "view": "taken",       "alert": None},
    AWAY_WITH_ITEM:   {"label": "이석 중(짐 있음)",  "color": "노랑",       "view": "taken",       "alert": None},
    HOARDING:         {"label": "사석화",            "color": "빨강",       "view": "taken",       "alert": "hoarding"},
    AWAY_EMPTY:       {"label": "이석 중(짐 없음)",  "color": "회청",       "view": "taken",       "alert": None},
    RETURN_DUE:       {"label": "반납 대상",         "color": "주황",       "view": "taken",       "alert": "return_due"},
    OFFLINE:          {"label": "감지 끊김",         "color": "회색",       "view": None,          "alert": None},
}
STATES = list(STATE_META)

# 판정 상태에서 생기는 알림 유형 (자동 해소 대상)
STATE_ALERT_TYPES = {m["alert"] for m in STATE_META.values() if m["alert"]}

ALERT_TYPE_LABELS = {
    "hoarding": "사석화",
    "unauthorized": "무단 사용",
    "item_only": "무단 물품 점유",
    "return_due": "반납 대상",
    "no_show": "미입실",
    "call": "이용자 호출",
}


def user_view(state, has_active_reservation):
    """사용자 지도 3분류. 판정명은 사용자에게 노출하지 않는다."""
    view = STATE_META[state]["view"]
    if view is None:  # OFFLINE
        return "taken" if has_active_reservation else "unavailable"
    return view


# ---------------------------------------------------------------- 판정 (§5.2, §5.3)

@dataclass(frozen=True)
class Reservation:
    id: int
    status: str
    start_at: int
    end_at: int
    checked_in_at: int | None


@dataclass(frozen=True)
class Detection:
    occupancy: str
    since: int
    updated_at: int


@dataclass(frozen=True)
class Judgement:
    state: str
    since: int
    elapsed_sec: int
    deadline: int | None


def _j(state, since, now, deadline=None):
    return Judgement(state=state, since=since, elapsed_sec=max(0, now - since), deadline=deadline)


def _timer(now, start, limit, before_state, after_state):
    """start부터 limit초 지나면 after_state. 전이 전엔 deadline, 전이 후엔 since = start + limit."""
    if now - start < limit:
        return _j(before_state, start, now, start + limit)
    return _j(after_state, start + limit, now)


def judge(res: Reservation | None, det: Detection | None, now: int, s: Settings) -> Judgement:
    if det is None or now - det.updated_at > s.stale_sec:
        return _j(OFFLINE, det.updated_at if det else now, now)

    occ = det.occupancy
    # 미래 시각으로 들어온 since(시계 오차)는 now로 자른다.
    since = min(det.since, now)

    if res is None:
        if occ == "person":
            return _timer(now, since, s.grace_sec, TEMP_OCCUPIED, UNAUTHORIZED)
        if occ == "item":
            return _j(ITEM_ONLY, since, now)
        return _j(AVAILABLE, since, now)

    if res.status == "reserved":
        checkin_deadline = res.start_at + s.checkin_limit_min * 60
        # SPEC-ASSUMPTION: 예약 좌석의 상태 시작 시각은 예약 시작 이후로 자른다(예약 전 점유 시간은 세지 않음).
        start = max(since, res.start_at)
        if occ in ("person", "item"):
            # SPEC-ASSUMPTION: 착석해 있어도 체크인 마감은 동일하므로 deadline을 함께 내려준다.
            return _j(AWAITING_CHECKIN, start, now, checkin_deadline)
        return _j(RESERVED, start, now, checkin_deadline)

    # in_use: 이석 시작점은 max(det.since, checked_in_at) — 체크인 시점부터 센다.
    # SPEC-ASSUMPTION: 명세는 짐만 있는 경우를 예로 들지만 empty·person에도 같은 기준을 적용한다.
    start = max(since, res.checked_in_at or res.start_at)
    if occ == "person":
        return _j(IN_USE, start, now)
    if occ == "item":
        return _timer(now, start, s.hoarding_min * 60, AWAY_WITH_ITEM, HOARDING)
    return _timer(now, start, s.empty_return_min * 60, AWAY_EMPTY, RETURN_DUE)
