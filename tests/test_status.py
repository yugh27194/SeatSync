"""judge() 순수 함수: 예약 3종(None/reserved/in_use) × 현장 3종(empty/occupied/unavailable) + 경계값."""
import pytest

from status import AVAILABLE, IN_USE, OK, UNAVAILABLE, Actual, Reservation, Settings, judge, user_view

S = Settings.from_dict({})
NOW = 1_800_000_000


def act(state, ago=0):
    return Actual(state=state, since=NOW - ago)


def res(status, start_ago=0, checked_in_ago=None):
    return Reservation(id=1, status=status, start_at=NOW - start_ago, end_at=NOW + 3600,
                       checked_in_at=None if checked_in_ago is None else NOW - checked_in_ago)


@pytest.mark.parametrize("r, a, seat_state, situation", [
    (None, "empty", AVAILABLE, OK),
    (None, "occupied", IN_USE, "unauthorized"),
    (None, "item", IN_USE, "unauthorized"),
    ("reserved", "item", IN_USE, "no_checkin"),
    (None, "unavailable", UNAVAILABLE, OK),
    ("reserved", "empty", IN_USE, OK),
    ("reserved", "occupied", IN_USE, "no_checkin"),
    ("reserved", "unavailable", UNAVAILABLE, "seat_unavailable"),
    ("in_use", "occupied", IN_USE, OK),
    ("in_use", "unavailable", UNAVAILABLE, "seat_unavailable"),
])
def test_matrix(r, a, seat_state, situation):
    reservation = res(r, checked_in_ago=10) if r else None
    j = judge(reservation, act(a, 5), NOW, S)
    assert (j.seat_state, j.situation) == (seat_state, situation)


def test_reserved_empty_has_checkin_deadline():
    r = res("reserved", start_ago=100)
    j = judge(r, act("empty", 1000), NOW, S)
    assert j.note == "입실 대기" and j.deadline == r.start_at + S.checkin_limit_min * 60


def test_away_boundary():
    limit = S.away_limit_min * 60
    r = res("in_use", checked_in_ago=99999)
    j = judge(r, act("empty", limit - 1), NOW, S)
    assert j.situation == OK and j.note == "잠시 자리 비움" and j.deadline == NOW - (limit - 1) + limit
    j = judge(r, act("empty", limit), NOW, S)
    assert j.situation == "away" and j.since == NOW
    j = judge(r, act("empty", limit + 120), NOW, S)
    assert j.situation == "away" and j.since == NOW - 120


def test_away_counts_from_checkin():
    """체크인 전부터 비어 있었어도 자리 비움은 체크인 시점부터 센다."""
    limit = S.away_limit_min * 60
    j = judge(res("in_use", checked_in_ago=60), act("empty", limit * 3), NOW, S)
    assert j.situation == OK and j.deadline == NOW - 60 + limit


def test_custom_away_limit():
    s = Settings.from_dict({"away_limit_min": 1})
    assert judge(res("in_use", checked_in_ago=999), act("empty", 61), NOW, s).situation == "away"


def test_future_since_clamped():
    j = judge(None, act("occupied", -30), NOW, S)
    assert j.since == NOW


def test_user_view_three_states():
    assert {user_view(x) for x in (AVAILABLE, IN_USE, UNAVAILABLE)} == {"available", "taken", "unavailable"}


def test_hoarding_boundary():
    limit = S.hoarding_min * 60
    r = res("in_use", checked_in_ago=99999)
    j = judge(r, act("item", limit - 1), NOW, S)
    assert j.situation == OK and j.note == "짐만 두고 자리 비움"
    assert judge(r, act("item", limit), NOW, S).situation == "hoarding"


def test_item_counts_from_checkin():
    limit = S.hoarding_min * 60
    j = judge(res("in_use", checked_in_ago=60), act("item", limit * 3), NOW, S)
    assert j.situation == OK and j.deadline == NOW - 60 + limit
