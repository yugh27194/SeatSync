"""좌석 상태 판정과 예약 대조. 순수 함수만 둔다 — DB·Django·현재 시각에 의존하지 않는다.

좌석 상태는 세 가지(빈자리 / 사용중 / 사용불가)이고, 각각 세부 상태를 가진다.

판정 흐름: 좌석 QR 체크인(예약 기록) × 카메라 사람 감지(현장 상태) → 비교 → 세부 상태.
  - 정상 이용   : QR 체크인 + 사람 감지
  - 일시 이석   : QR 체크인했는데 사람 미감지 (장기 이석 기준 전)
  - 장기 이석   : 사람 미감지가 기준 시간 이상 → ! 관리자 확인
  - 무단 점유   : QR 체크인 없이 사람(또는 짐)이 기준 시간 이상 감지 → ! 관리자 확인
  - 판단 불가   : 가림·인식 실패·카메라 오류(UNKNOWN)가 기준 시간 이상 → ! 관리자 확인
명확한 경우는 자동으로 좌석 상태에 반영하고, 애매하거나 QR과 카메라가 어긋나는 경우만 '처리 필요'(관리자 확인)로 넘긴다.
"""
from dataclasses import dataclass, fields

# ---------------------------------------------------------------- 설정값

DEFAULT_SETTINGS = {
    "checkin_limit_min": 15,
    "away_limit_min": 30,
    "hoarding_min": 30,
    "unauthorized_min": 10,
    "unknown_min": 3,
    "auto_return_min": 15,
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

# key: (라벨, 단위, 설명, 최소, 최대). 단위가 "분"인 값은 15초(0.25분) 단위로 정할 수 있다 — 시연(3분)에서 짧게 쓰려고.
SETTINGS_META = {
    "checkin_limit_min": ("체크인 제한", "분", "예약 후 이 시간 안에 체크인하지 않으면 미입실", 0.25, 120),
    "away_limit_min": ("장기 이석 기준", "분",
                       "체크인 후 이 시간 넘게 자리를 비우면 장기 이석", 0.25, 240),
    "hoarding_min": ("사석화 기준", "분", "짐만 두고 이 시간 넘게 자리를 비우면 사석화", 0.25, 240),
    "unauthorized_min": ("무단 점유 기준", "분",
                         "체크인 없이 이 시간 넘게 앉아 있으면 무단 점유(예약석은 체크인 누락)", 0.25, 120),
    "unknown_min": ("판단 불가 기준", "분",
                    "카메라가 이 시간 넘게 좌석을 확인하지 못하면 판단 불가", 0.25, 60),
    "auto_return_min": ("자동 강제 반납", "분",
                        "미입실·장기 이석·사석화가 이 시간 넘게 이어지면 자동 반납 (0 = 끔)", 0, 240),
    "default_use_min": ("기본 이용 시간", "분", "예약 1회 이용 시간", 1, 720),
    "extend_min": ("연장 시간", "분", "연장 1회당 늘어나는 시간", 1, 360),
    "extend_window_min": ("연장 가능 시점", "분", "남은 시간이 이 값 이하일 때만 연장 가능", 0.25, 240),
    "max_extends": ("최대 연장 횟수", "회", "예약 1건당 직접 연장할 수 있는 횟수", 0, 10),
    "warning_limit": ("정지 권장 경고 수", "회", "경고가 이만큼 쌓이면 이용 정지 권장", 1, 20),
    "suspend_days": ("기본 정지 기간", "일", "이용 정지 기본 기간", 1, 90),
    "prewarn_min": ("사전 경고 시점", "분", "기준 시간 이만큼 전에 본인에게 알림", 0.25, 60),
    "waitlist_hold_min": ("빈자리 안내 유지", "분", "대기자가 먼저 예약할 수 있는 시간", 0.25, 30),
    "open_hour": ("운영 시작", "시", "혼잡도 통계 시작 시각", 0, 23),
    "close_hour": ("운영 종료", "시", "혼잡도 통계 종료 시각 (24 = 자정)", 1, 24),
}
TIME_KEYS = frozenset(k for k, m in SETTINGS_META.items() if m[1] == "분")
TIME_STEP = 0.25  # 15초


def fmt_min(v):
    """분(15초 단위 소수) → '15분', '1분 30초', '45초'."""
    total = int(round(float(v) * 60))
    m, sec = divmod(total, 60)
    if m and sec:
        return f"{m}분 {sec}초"
    return f"{m}분" if m else f"{sec}초"


def fmt_sec(sec):
    """남은/지난 시간(초) → '45초', '3분', '1시간 5분' (1분 이상은 분 단위 올림)."""
    sec = max(0, int(sec))
    if sec < 60:
        return f"{max(1, sec)}초"
    m = (sec + 59) // 60
    return f"{m // 60}시간 {m % 60}분" if m >= 60 else f"{m}분"


@dataclass(frozen=True)
class Settings:
    checkin_limit_min: float
    away_limit_min: float
    hoarding_min: float
    unauthorized_min: float
    unknown_min: float
    auto_return_min: float
    default_use_min: float
    extend_min: float
    extend_window_min: float
    max_extends: int
    warning_limit: int
    suspend_days: int
    prewarn_min: float
    waitlist_hold_min: float
    open_hour: int
    close_hour: int

    @classmethod
    def from_dict(cls, d):
        merged = {**DEFAULT_SETTINGS, **{k: v for k, v in (d or {}).items() if k in DEFAULT_SETTINGS}}
        out = {}
        for f in fields(cls):
            v = float(merged[f.name])
            out[f.name] = (int(v) if v.is_integer() else v) if f.name in TIME_KEYS else int(v)
        return cls(**out)

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def sec(self, key):
        """분 단위 설정값을 초(정수)로."""
        return int(round(getattr(self, key) * 60))


# ---------------------------------------------------------------- 좌석 상태 3가지 + 세부 상태

AVAILABLE, IN_USE, UNAVAILABLE = "available", "in_use", "unavailable"
SEAT_STATES = {AVAILABLE: "빈자리", IN_USE: "사용중", UNAVAILABLE: "사용불가"}

OK = "ok"

# 세부 상태: (상위 상태, 라벨, 처리 필요 여부, 설명)
DETAILS = {
    "empty":            (AVAILABLE,   "빈자리",           False, "예약할 수 있는 빈 좌석이에요."),
    "using":            (IN_USE,      "정상 이용",        False, "QR 체크인 후 이용 중이에요."),
    "waiting":          (IN_USE,      "입실 대기",        False, "예약 후 체크인을 기다리는 중이에요."),
    "detected":         (IN_USE,      "착석 감지(체크인 전)", False,
                         "예약 없이 사람이 앉았어요. 체크인하지 않으면 '무단 점유'가 돼요."),
    "seated_unchecked": (IN_USE,      "착석(체크인 전)",  False,
                         "예약 좌석에 앉았지만 체크인 전이에요. 시간이 지나면 '체크인 누락'이 돼요."),
    "away_short":       (IN_USE,      "일시 이석",        False,
                         "체크인한 사람이 자리를 비웠어요. 시간이 지나면 '장기 이석'이 돼요."),
    "item":             (IN_USE,      "짐만 있음",        False,
                         "사람은 없고 짐만 있어요."),
    "unauthorized":     (IN_USE,      "무단 점유",        True,  "체크인 없이 자리를 오래 쓰고 있어요."),
    "no_checkin":       (IN_USE,      "체크인 누락",      True,  "예약 좌석에 앉았지만 오래 체크인하지 않았어요."),
    "no_show":          (IN_USE,      "미입실",           True,  "예약자가 체크인 시간 안에 오지 않았어요."),
    "away":             (IN_USE,      "장기 이석",        True,  "체크인한 사람이 오래 자리를 비웠어요."),
    "hoarding":         (IN_USE,      "사석화",           True,  "짐만 두고 오래 자리를 비웠어요."),
    # 판단 불가의 상위 상태는 마지막으로 확인된 판정을 따른다(빈자리였으면 빈자리 그대로). 여기 값은 기본값일 뿐.
    "unknown":          (IN_USE,      "판단 불가",        True,
                         "카메라가 좌석을 확인하지 못해요(가림·인식 오류). 현장 확인이 필요해요."),
    "broken":           (UNAVAILABLE, "고장",             False, "고장으로 쓸 수 없는 좌석이에요."),
    "maintenance":      (UNAVAILABLE, "점검·청소",        False, "점검·청소 중인 좌석이에요."),
    "blocked":          (UNAVAILABLE, "사용 중지",        False, "사용을 막아 둔 좌석이에요."),
    "seat_unavailable": (UNAVAILABLE, "예약 좌석 사용불가", True, "예약된 좌석을 쓸 수 없어요. 다른 좌석으로 옮겨 주세요."),
}
ISSUES = [k for k, v in DETAILS.items() if v[2]]
# 좌석 지도에서 붉게 강조하고 '!'(확인 필요)를 붙이는 세부 상태: 장기 이석·무단 점유·판단 불가 등.
# 처리 필요(ISSUES)에 더해, 아직 기준 시간 전인 '일시 이석'·'짐만 있음'도 눈으로 확인하도록 표시한다.
CHECK = frozenset(ISSUES) | {"away_short", "item"}
UNAVAILABLE_REASONS = ("broken", "maintenance", "blocked")

# 관리자 화면의 상태 분류 4가지(+사용불가): 정상(초록) · 이석(주황, 일시/장기) · 무단 점유(빨강) · 판단 불가(짙은 회색)
CATEGORIES = {"normal": "정상", "away": "이석", "unauthorized": "무단 점유", "unknown": "판단 불가", "unavailable": "사용불가"}
_CATEGORY_OF = {
    "empty": "normal", "using": "normal", "waiting": "normal", "detected": "normal", "seated_unchecked": "normal",
    "away_short": "away", "item": "away", "away": "away", "hoarding": "away", "no_show": "away",
    "unauthorized": "unauthorized", "no_checkin": "unauthorized",
    "unknown": "unknown",
    "broken": "unavailable", "maintenance": "unavailable", "blocked": "unavailable", "seat_unavailable": "unavailable",
}
AWAY_LONG = frozenset({"away", "hoarding", "no_show"})  # 장기 이석 (나머지 이석은 일시 이석)


def category(detail):
    """세부 상태 → (분류 코드, 표시 이름). 이석은 '이석(일시)' / '이석(장기)'로 나눈다."""
    cat = _CATEGORY_OF[detail]
    if cat == "away":
        return cat, "이석(장기)" if detail in AWAY_LONG else "이석(일시)"
    return cat, CATEGORIES[cat]


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
    deadline: int | None = None  # 다음 변화 예정 시각 (체크인 마감, 장기 이석·사석화·무단 점유·판단 불가 기준)
    stale: bool = False          # 카메라 감지 확인 불가 — 마지막으로 확인된 상태를 보여 주는 중
    next_detail: str | None = None  # deadline에 바뀔 세부 상태

    @property
    def situation(self):
        """처리 필요 유형(= detail) 또는 'ok'."""
        return self.detail if DETAILS[self.detail][2] else OK

    @property
    def needs_action(self):
        return DETAILS[self.detail][2]

    @property
    def check(self):
        """좌석 지도에 '!'(확인 필요)로 강조할지."""
        return self.detail in CHECK


def _j(detail, since, deadline=None, next_detail=None):
    return Judgement(DETAILS[detail][0], detail, since, deadline, False, next_detail if deadline else None)


def judge(res: Reservation | None, actual: Actual, now: int, s: Settings) -> Judgement:
    """res: 해당 좌석의 활성 예약(reserved/in_use)만. actual: 현장 상태."""
    j = _judge(res, actual, now, s)
    stale = actual.stale_since
    if stale is None or actual.state == "unavailable":
        return j
    # 카메라가 좌석을 판단하지 못함(UNKNOWN·수신 끊김): 마지막으로 확인된 상태를 보여 주다가,
    # 기준 시간이 지나도 회복되지 않으면 '판단 불가'로 관리자 확인. 이미 처리 필요였던 판정은 그대로 둔다.
    unknown_at = min(stale, now) + s.sec("unknown_min")
    if j.needs_action:
        return Judgement(j.seat_state, j.detail, j.since, j.deadline, True, j.next_detail)
    if now >= unknown_at:
        return Judgement(j.seat_state, "unknown", unknown_at, None, True)
    return Judgement(j.seat_state, j.detail, j.since, unknown_at, True, "unknown")


def _timed(short, issue, start, limit, now, actual):
    """기준 시간(limit초) 동안은 short, 넘으면 issue.
    카메라 감지를 믿을 수 없게 된 뒤로는 기준 시간을 세지 않는다(감지 끊김을 이석·점유로 오해하지 않게).
    끊기기 전에 이미 기준 시간을 넘었다면 그대로 처리 필요."""
    if actual.stale_since is not None and actual.stale_since < start + limit:
        return _j(short, start)
    if now - start < limit:
        return _j(short, start, start + limit, issue)
    return _j(issue, start + limit)


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
            if mark == "issue":  # 관리자가 무단 점유로 지정 → 기준 시간을 기다리지 않음
                return _j("unauthorized", since)
            # QR 체크인 없이 사람(또는 짐)이 감지됨 → 기준 시간이 지나면 무단 점유
            return _timed("detected" if a == "occupied" else "item", "unauthorized", since,
                          s.sec("unauthorized_min"), now, actual)
        return _j("empty", since)

    if res.status == "reserved":
        checkin_deadline = res.start_at + s.sec("checkin_limit_min")
        start = max(since, res.start_at)
        if a in ("occupied", "item"):
            if mark == "ok":
                return _j("seated_unchecked", start)
            if mark == "issue":
                return _j("no_checkin", start)
            # 예약자가 앉고 QR을 찍기까지의 시간은 기다린다 → 기준 시간이 지나도 체크인 없으면 체크인 누락
            return _timed("seated_unchecked", "no_checkin", start, s.sec("unauthorized_min"), now, actual)
        if now >= checkin_deadline:  # 시간 안에 아무도 오지 않음 → 확인 필요 (자동 취소하지 않고 관리자가 판단)
            return _j("no_show", checkin_deadline)
        return _j("waiting", res.start_at, checkin_deadline, "no_show")

    # in_use(QR 체크인함): 자리 비움은 체크인 시점 이후부터 센다
    start = max(since, res.checked_in_at or res.start_at)
    if a == "occupied":
        return _j("unauthorized" if mark == "issue" else "using", start)
    if a == "item":
        limit, short, issue = s.sec("hoarding_min"), "item", "hoarding"
    else:
        limit, short, issue = s.sec("away_limit_min"), "away_short", "away"
    if mark == "issue":
        return _j(issue, start)
    return _timed(short, issue, start, limit, now, actual)


def user_view(seat_state):
    """사용자 지도 분류. 세부 상태·처리 필요 여부는 일반 사용자에게 보이지 않는다."""
    return {AVAILABLE: "available", IN_USE: "taken", UNAVAILABLE: "unavailable"}[seat_state]
