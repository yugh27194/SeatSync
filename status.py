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
