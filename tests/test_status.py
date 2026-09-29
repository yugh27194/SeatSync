"""judge() 순수 함수 테스트: 예약 3종 × 점유 3종 + 경계값."""
import pytest

from status import (AVAILABLE, AWAITING_CHECKIN, AWAY_EMPTY, AWAY_WITH_ITEM, HOARDING, IN_USE, ITEM_ONLY,
                    OFFLINE, RESERVED, RETURN_DUE, TEMP_OCCUPIED, UNAUTHORIZED, Detection, Reservation, Settings,
                    judge)

S = Settings.from_dict({})
NOW = 1_800_000_000


def det(occ, ago, updated_ago=0):
    return Detection(occupancy=occ, since=NOW - ago, updated_at=NOW - updated_ago)


def res(status, checked_in_ago=None, start_ago=0):
    return Reservation(id=1, status=status, start_at=NOW - start_ago, end_at=NOW + 3600,
                       checked_in_at=None if checked_in_ago is None else NOW - checked_in_ago)


# ---------------- 9 조합

def test_none_person_temp():
    j = judge(None, det("person", 5), NOW, S)
    assert j.state == TEMP_OCCUPIED
    assert j.deadline == NOW - 5 + S.grace_sec


def test_none_person_unauthorized():
    j = judge(None, det("person", 600), NOW, S)
    assert j.state == UNAUTHORIZED
    assert j.since == NOW - 600 + S.grace_sec
    assert j.elapsed_sec == 600 - S.grace_sec


def test_none_item():
    assert judge(None, det("item", 10), NOW, S).state == ITEM_ONLY


def test_none_empty():
    j = judge(None, det("empty", 10), NOW, S)
    assert j.state == AVAILABLE and j.deadline is None


def test_reserved_person():
    assert judge(res("reserved"), det("person", 10), NOW, S).state == AWAITING_CHECKIN


def test_reserved_item():
    assert judge(res("reserved"), det("item", 10), NOW, S).state == AWAITING_CHECKIN


def test_reserved_empty():
    r = res("reserved", start_ago=100)
    j = judge(r, det("empty", 1000), NOW, S)
    assert j.state == RESERVED
    assert j.deadline == r.start_at + S.checkin_limit_min * 60


def test_in_use_person():
    j = judge(res("in_use", checked_in_ago=100), det("person", 50), NOW, S)
    assert j.state == IN_USE and j.deadline is None


def test_in_use_item_away():
    j = judge(res("in_use", checked_in_ago=9999), det("item", 60), NOW, S)
    assert j.state == AWAY_WITH_ITEM
    assert j.deadline == NOW - 60 + S.hoarding_min * 60


def test_in_use_empty_away():
    j = judge(res("in_use", checked_in_ago=9999), det("empty", 60), NOW, S)
    assert j.state == AWAY_EMPTY
    assert j.deadline == NOW - 60 + S.empty_return_min * 60


# ---------------- 경계값

def test_grace_boundary():
    g = S.grace_sec
    assert judge(None, det("person", g - 1), NOW, S).state == TEMP_OCCUPIED
    assert judge(None, det("person", g), NOW, S).state == UNAUTHORIZED
    assert judge(None, det("person", g + 1), NOW, S).state == UNAUTHORIZED


def test_hoarding_boundary():
    h = S.hoarding_min * 60
    r = res("in_use", checked_in_ago=99999)
    assert judge(r, det("item", h - 1), NOW, S).state == AWAY_WITH_ITEM
    j = judge(r, det("item", h), NOW, S)
    assert j.state == HOARDING
    assert j.since == NOW - h + h and j.elapsed_sec == 0
    j = judge(r, det("item", h + 120), NOW, S)
    assert j.state == HOARDING and j.elapsed_sec == 120 and j.deadline is None


def test_empty_return_boundary():
    e = S.empty_return_min * 60
    r = res("in_use", checked_in_ago=99999)
    assert judge(r, det("empty", e - 1), NOW, S).state == AWAY_EMPTY
    assert judge(r, det("empty", e), NOW, S).state == RETURN_DUE


def test_stale_boundary():
    st = S.stale_sec
    assert judge(None, det("empty", 10, updated_ago=st), NOW, S).state == AVAILABLE
    j = judge(None, det("empty", 10, updated_ago=st + 1), NOW, S)
    assert j.state == OFFLINE and j.since == NOW - st - 1


def test_no_detection_offline():
    j = judge(res("in_use", checked_in_ago=10), None, NOW, S)
    assert j.state == OFFLINE and j.since == NOW


def test_item_before_checkin_counts_from_checkin():
    """체크인 전부터 짐이 있었어도 이석 시간은 체크인 시점부터 센다."""
    h = S.hoarding_min * 60
    r = res("in_use", checked_in_ago=60)
    j = judge(r, det("item", h * 3), NOW, S)
    assert j.state == AWAY_WITH_ITEM
    assert j.deadline == NOW - 60 + h
    r2 = res("in_use", checked_in_ago=h + 5)
    assert judge(r2, det("item", h * 3), NOW, S).state == HOARDING


def test_custom_settings_applied():
    s = Settings.from_dict({"hoarding_min": 1})
    r = res("in_use", checked_in_ago=9999)
    assert judge(r, det("item", 61), NOW, s).state == HOARDING


def test_future_since_clamped():
    j = judge(None, det("person", -30), NOW, S)
    assert j.state == TEMP_OCCUPIED and j.elapsed_sec == 0


@pytest.mark.parametrize("state", [AVAILABLE, IN_USE, HOARDING, OFFLINE])
def test_state_meta(state):
    from status import STATE_META
    assert STATE_META[state]["label"]
