"""judge() 순수 함수: 예약 3종 × 현장 4종 × 관리자 지정(mark) + 경계값."""
import pytest

from seats.status import AVAILABLE, IN_USE, UNAVAILABLE, Actual, Reservation, Settings, judge, user_view

S = Settings.from_dict({})
NOW = 1_800_000_000


def act(state, ago=0, mark=None, reason=None):
    return Actual(state=state, since=NOW - ago, mark=mark, reason=reason)


def res(status, start_ago=0, checked_in_ago=None):
    return Reservation(id=1, status=status, start_at=NOW - start_ago, end_at=NOW + 3600,
                       checked_in_at=None if checked_in_ago is None else NOW - checked_in_ago)


@pytest.mark.parametrize("r, a, mark, seat_state, detail", [
    (None, "empty", None, AVAILABLE, "empty"),
    (None, "occupied", None, IN_USE, "unauthorized"),        # 카메라가 본 미예약 착석
    (None, "occupied", "ok", IN_USE, "using"),               # 관리자가 확인한 이용 → 처리 필요 아님
    (None, "item", None, IN_USE, "unauthorized"),
    (None, "item", "ok", IN_USE, "item"),                    # 짐만 있음은 사용중의 하위 상태
    (None, "unavailable", None, UNAVAILABLE, "blocked"),
    ("reserved", "empty", None, IN_USE, "waiting"),
    ("reserved", "occupied", None, IN_USE, "no_checkin"),
    ("reserved", "occupied", "ok", IN_USE, "seated_unchecked"),
    ("reserved", "item", None, IN_USE, "no_checkin"),
    ("reserved", "unavailable", None, UNAVAILABLE, "seat_unavailable"),
    ("in_use", "occupied", None, IN_USE, "using"),
    ("in_use", "occupied", "ok", IN_USE, "using"),
    ("in_use", "occupied", "issue", IN_USE, "unauthorized"),  # 예약석을 다른 사람이 점유
    ("in_use", "empty", None, IN_USE, "away_short"),
    ("in_use", "empty", "issue", IN_USE, "away"),             # 관리자가 이탈로 지정 → 즉시
    ("in_use", "item", None, IN_USE, "item"),
    ("in_use", "item", "issue", IN_USE, "hoarding"),          # 관리자가 사석화로 지정 → 즉시
    ("in_use", "unavailable", None, UNAVAILABLE, "seat_unavailable"),
])
def test_matrix(r, a, mark, seat_state, detail):
    reservation = res(r, checked_in_ago=10) if r else None
    j = judge(reservation, act(a, 5, mark), NOW, S)
    assert (j.seat_state, j.detail) == (seat_state, detail)


@pytest.mark.parametrize("reason", ["broken", "maintenance", "blocked"])
def test_unavailable_reasons(reason):
    j = judge(None, act("unavailable", reason=reason), NOW, S)
    assert j.seat_state == UNAVAILABLE and j.detail == reason and not j.needs_action


def test_needs_action_flags():
    assert judge(None, act("occupied"), NOW, S).needs_action
    assert not judge(None, act("occupied", mark="ok"), NOW, S).needs_action
    assert judge(None, act("occupied"), NOW, S).situation == "unauthorized"
    assert judge(None, act("empty"), NOW, S).situation == "ok"


def test_waiting_has_checkin_deadline():
    r = res("reserved", start_ago=100)
    j = judge(r, act("empty", 1000), NOW, S)
    assert j.detail == "waiting" and j.deadline == r.start_at + S.checkin_limit_min * 60


def test_away_boundary():
    limit = S.away_limit_min * 60
    r = res("in_use", checked_in_ago=99999)
    j = judge(r, act("empty", limit - 1), NOW, S)
    assert j.detail == "away_short" and j.deadline == NOW - (limit - 1) + limit
    assert judge(r, act("empty", limit), NOW, S).detail == "away"
    j = judge(r, act("empty", limit + 120), NOW, S)
    assert j.detail == "away" and j.since == NOW - 120


def test_hoarding_boundary():
    limit = S.hoarding_min * 60
    r = res("in_use", checked_in_ago=99999)
    assert judge(r, act("item", limit - 1), NOW, S).detail == "item"
    assert judge(r, act("item", limit), NOW, S).detail == "hoarding"
    # 관리자가 '짐만 있음'(ok)으로 지정해도 기준 시간이 지나면 사석화
    assert judge(r, act("item", limit, mark="ok"), NOW, S).detail == "hoarding"


def test_away_counts_from_checkin():
    limit = S.away_limit_min * 60
    j = judge(res("in_use", checked_in_ago=60), act("empty", limit * 3), NOW, S)
    assert j.detail == "away_short" and j.deadline == NOW - 60 + limit


def test_future_since_clamped():
    assert judge(None, act("occupied", -30), NOW, S).since == NOW


def test_user_view_three_states():
    assert {user_view(x) for x in (AVAILABLE, IN_USE, UNAVAILABLE)} == {"available", "taken", "unavailable"}
