"""judge() 순수 함수: 예약 3종 × 현장 4종 × 관리자 지정(mark) + 경계값."""
import pytest

from seats.status import AVAILABLE, IN_USE, UNAVAILABLE, Actual, Reservation, Settings, judge, user_view

S = Settings.from_dict({})
NOW = 1_800_000_000


def act(state, ago=0, mark=None, reason=None, stale_ago=None):
    return Actual(state=state, since=NOW - ago, mark=mark, reason=reason,
                  stale_since=None if stale_ago is None else NOW - stale_ago)


def res(status, start_ago=0, checked_in_ago=None):
    return Reservation(id=1, status=status, start_at=NOW - start_ago, end_at=NOW + 3600,
                       checked_in_at=None if checked_in_ago is None else NOW - checked_in_ago)


@pytest.mark.parametrize("r, a, mark, seat_state, detail", [
    (None, "empty", None, AVAILABLE, "empty"),
    (None, "occupied", None, IN_USE, "detected"),            # QR 체크인 없이 착석 → 기준 시간 전엔 착석 감지
    (None, "occupied", "ok", IN_USE, "using"),               # 관리자가 확인한 이용 → 처리 필요 아님
    (None, "occupied", "issue", IN_USE, "unauthorized"),     # 관리자가 무단 점유로 지정 → 즉시
    (None, "item", None, AVAILABLE, "item_left"),            # 예약 없는 좌석의 짐: 이용자에게는 빈자리
    (None, "item", "ok", AVAILABLE, "item_left"),
    (None, "unavailable", None, UNAVAILABLE, "blocked"),
    ("reserved", "empty", None, IN_USE, "waiting"),
    ("reserved", "occupied", None, IN_USE, "seated_unchecked"),  # 앉고 QR을 찍기까지 기다림
    ("reserved", "occupied", "ok", IN_USE, "seated_unchecked"),
    ("reserved", "item", None, IN_USE, "waiting"),          # 짐만 두고 체크인 안 함 = 입실 대기 → 미입실
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
    long = S.unauthorized_min * 60
    assert judge(None, act("occupied", long), NOW, S).needs_action
    assert not judge(None, act("occupied"), NOW, S).needs_action
    assert not judge(None, act("occupied", long, mark="ok"), NOW, S).needs_action
    assert judge(None, act("occupied", long), NOW, S).situation == "unauthorized"
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
    limit = S.away_limit_min * 60  # 사석화 기준은 장기 이석 기준과 통합
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


# ---------------------------------------------------------------- QR 체크인 × 카메라 감지 판정 기준

def test_unauthorized_boundary():
    """QR 체크인 없이 사람(또는 짐)이 기준 시간 이상 감지되면 무단 점유."""
    limit = S.unauthorized_min * 60
    j = judge(None, act("occupied", limit - 1), NOW, S)
    assert j.detail == "detected" and not j.needs_action and not j.check
    assert j.deadline == NOW + 1 and j.next_detail == "unauthorized"
    j = judge(None, act("occupied", limit + 30), NOW, S)
    assert j.detail == "unauthorized" and j.since == NOW - 30



def test_unowned_item_boundary():
    """예약 없는 좌석의 짐만: 이용자에게는 빈자리, 장기 이석 기준이 지나면 관리자에게 '이석(장기) · 주인 없는 짐'."""
    from seats.status import category, full_label
    limit = S.away_limit_min * 60
    j = judge(None, act("item", limit - 1), NOW, S)
    assert (j.seat_state, j.detail, j.needs_action) == (AVAILABLE, "item_left", False)
    assert category("item_left") == ("empty", "빈자리")
    j = judge(None, act("item", limit), NOW, S)
    assert (j.seat_state, j.detail, j.needs_action) == (AVAILABLE, "unowned_item", True)
    assert full_label("unowned_item") == "이석(장기) · 주인 없는 짐"
    assert judge(None, act("item", 10, mark="issue"), NOW, S).detail == "unowned_item"


def test_long_away_labels_merged():
    """사석화·미입실·주인 없는 짐·장기 이석은 관리자 화면에서 모두 이석(장기) + 사유."""
    from seats.status import category, full_label
    for d in ("away", "hoarding", "no_show", "unowned_item"):
        assert category(d) == ("away", "이석(장기)")
    assert full_label("no_show") == "이석(장기) · 예약 후 미입실"
    assert full_label("hoarding") == "이석(장기) · 짐만 두고 자리 비움"
    assert full_label("item") == "이석(일시) · 짐만 두고 자리 비움"


def test_no_checkin_boundary():
    """예약석에 앉았지만 기준 시간 넘게 QR 체크인하지 않으면 체크인 누락."""
    limit = S.unauthorized_min * 60
    r = res("reserved", start_ago=limit + 100)
    j = judge(r, act("occupied", limit - 1), NOW, S)
    assert j.detail == "seated_unchecked" and j.next_detail == "no_checkin"
    assert judge(r, act("occupied", limit), NOW, S).detail == "no_checkin"
    # 예약 전부터 앉아 있었어도 예약 시작 시점부터 센다
    assert judge(res("reserved", start_ago=60), act("occupied", limit * 2), NOW, S).detail == "seated_unchecked"
    assert judge(r, act("occupied", limit, mark="issue"), NOW, S).detail == "no_checkin"


def test_away_short_then_long():
    """QR 체크인 후 사람 미감지: 일시 이석 → 기준 시간이 지나면 장기 이석."""
    r = res("in_use", checked_in_ago=99999)
    j = judge(r, act("empty", 60), NOW, S)
    assert j.detail == "away_short" and j.next_detail == "away" and not j.needs_action
    assert judge(r, act("empty", S.away_limit_min * 60), NOW, S).detail == "away"


def test_unknown_after_threshold():
    """카메라 판단 불가(UNKNOWN)가 기준 시간 이상 이어지면 '판단 불가'로 관리자 확인."""
    lim = S.unknown_min * 60
    r = res("in_use", checked_in_ago=600)
    j = judge(r, act("occupied", 600, stale_ago=lim - 1), NOW, S)
    assert j.detail == "using" and j.stale and j.deadline == NOW + 1 and j.next_detail == "unknown"
    j = judge(r, act("occupied", 600, stale_ago=lim), NOW, S)
    assert j.detail == "unknown" and j.stale and j.needs_action and j.check and j.seat_state == IN_USE
    assert j.since == NOW
    # 예약 없는 빈자리가 판단 불가여도 빈자리로 남는다(예약은 가능) — 관리자 확인만 요청
    j = judge(None, act("empty", 600, stale_ago=lim), NOW, S)
    assert j.detail == "unknown" and j.seat_state == AVAILABLE


def test_unknown_does_not_escalate_timers():
    """판단 불가 중에는 장기 이석·무단 점유 기준 시간을 세지 않는다."""
    r = res("in_use", checked_in_ago=99999)
    j = judge(r, act("empty", 3600, stale_ago=3500), NOW, S)  # 끊긴 뒤 3500초 → 장기 이석 아님, 판단 불가
    assert j.detail == "unknown"
    j = judge(None, act("occupied", 3600, stale_ago=3500), NOW, S)
    assert j.detail == "unknown"


def test_issue_before_outage_is_kept():
    """끊기기 전에 이미 처리 필요였던 판정은 판단 불가로 덮지 않는다."""
    limit = S.unauthorized_min * 60
    j = judge(None, act("occupied", limit + 600, stale_ago=60), NOW, S)
    assert j.detail == "unauthorized" and j.stale


def test_unknown_ignored_for_unavailable():
    j = judge(None, act("unavailable", 600, reason="broken", stale_ago=3600), NOW, S)
    assert j.detail == "broken" and not j.needs_action
