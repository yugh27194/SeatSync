"""시연 시나리오별 처리 흐름 (docs/SCENARIOS.md). 판정 기준은 시연용(장기 이석·무단 점유 1분)."""
import pytest
from conftest import ADMIN_CODE, ApiClient, admin_seat, err, login, qr_token

from seats.models import Alert, Notification, Reservation, Seat
from seats.scenarios import apply_demo_settings, setup_scenario


def seat_no(label):
    return Seat.objects.get(label=label, active=True).no


@pytest.fixture
def manager():
    c = login(ApiClient(), "manager")
    assert c.jpost("/api/admin-mode/unlock", {"code": ADMIN_CODE}).status_code == 200
    return c


@pytest.fixture
def ua():
    return login(ApiClient(), "userA")


@pytest.fixture
def ub():
    return login(ApiClient(), "userB")


@pytest.fixture
def uc():
    return login(ApiClient(), "userC")


@pytest.fixture(autouse=True)
def demo(clock):
    apply_demo_settings()


def test_manager_account_needs_code():
    c = login(ApiClient(), "manager")
    assert c.get("/api/admin/seats").status_code == 403  # 계정만으로는 관리자 탭 권한이 없다


def test_1_someone_sits_in_reserved_seat(clock, manager, ua, ub):
    """userA 예약(체크인 전) 좌석에 userB가 앉음 → userA가 관리자에게 알림 → 관리자 팝업 → 착석자 퇴실 → userA 체크인."""
    setup_scenario("1", clock())
    a1 = seat_no("A-1")
    s = admin_seat(manager, a1)
    assert s["detail"] == "seated_unchecked" and s["category"] == "normal"  # 아직은 예약자가 앉았는지 알 수 없다
    # userB가 A-1 QR을 찍어도 예약된 좌석이라 예약할 수 없다
    assert ub.jget(f"/api/seats/{a1}?t={qr_token(a1)}")["page_mode"] == "reserved_by_other"
    assert err(ub.jpost("/api/reservations", {"seat_no": a1, "qr_token": qr_token(a1)})) == "SEAT_TAKEN"
    assert err(ub.jpost("/api/calls", {"seat_no": a1, "kind": "seat_taken"})) == "NOT_YOUR_SEAT"
    # userA: "내 자리에 다른 사람이 앉아 있어요"
    assert ua.jget(f"/api/seats/{a1}")["page_mode"] == "mine_checkin"
    r = ua.jpost("/api/calls", {"seat_no": a1, "kind": "seat_taken", "memo": "누가 앉아 있어요"})
    assert r.status_code == 201
    call = next(a for a in manager.jget("/api/admin/alerts?open=1")["alerts"] if a["type"] == "call")
    assert call["call_kind"] == "seat_taken" and call["call_label"] == "내 예약 좌석에 다른 사람이 앉아 있음"
    assert call["caller"]["name"] == "사용자A" and call["reservation"]["user"]["name"] == "사용자A"
    # 1분이 지나도 체크인이 없으면 시스템도 무단 점유(체크인 누락)로 판정
    clock.advance(60)
    s = admin_seat(manager, a1)
    assert s["detail"] == "no_checkin" and s["category"] == "unauthorized" and s["needs_action"]
    # 관리자: 착석자 퇴실 안내 → 비어 있음 + 신고 처리 완료
    assert manager.jpost(f"/api/admin/seats/{a1}/state", {"detail": "empty"}).status_code == 200
    assert manager.jpost(f"/api/admin/alerts/{call['id']}/resolve", {}).status_code == 200
    # userA가 좌석 QR로 체크인 → 정상 이용
    rid = Reservation.objects.get(user__student_no="userA", status="reserved").id
    assert ua.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(a1)}).status_code == 200
    s = admin_seat(manager, a1)
    assert s["detail"] == "using" and s["category"] == "normal" and not s["needs_action"]


def test_1b_someone_sits_while_reserver_away(clock, manager, ua):
    """체크인 후 잠깐 나간 사이 다른 사람이 앉으면 카메라로는 구분이 안 됨 → 예약자 신고 → 관리자가 무단 점유로 지정."""
    setup_scenario("1b", clock())
    a1 = seat_no("A-1")
    assert admin_seat(manager, a1)["detail"] == "using"
    assert ua.jpost("/api/calls", {"seat_no": a1, "kind": "seat_taken"}).status_code == 201
    assert manager.jpost(f"/api/admin/seats/{a1}/state", {"detail": "unauthorized"}).status_code == 200
    s = admin_seat(manager, a1)
    assert s["detail"] == "unauthorized" and s["category"] == "unauthorized"
    assert Notification.objects.filter(user__student_no="userA", title__contains="다른 이용이 확인").exists()


def test_2a_checked_in_but_not_seated(clock, manager, uc):
    """userC 체크인 후 자리에 없음 → 일시 이석 → 1분 뒤 장기 이석(관리자 확인) → 2분 뒤 자동 반납."""
    setup_scenario("2a", clock())
    b2 = seat_no("B-2")
    s = admin_seat(manager, b2)
    assert (s["detail"], s["category_label"]) == ("away_short", "이석(일시)") and s["next_label"] == "이석(장기) · 자리 비움"
    clock.advance(45)  # 기준 15초 전 → 본인 사전 경고
    uc.get("/api/seats")
    assert Notification.objects.filter(user__student_no="userC", kind="prewarn").exists()
    clock.advance(15)
    s = admin_seat(manager, b2)
    assert (s["detail"], s["category_label"]) == ("away", "이석(장기)") and s["needs_action"]
    assert Alert.objects.filter(seat_id=b2, type="away", resolved_at__isnull=True).exists()
    clock.advance(120)
    admin_seat(manager, b2)
    assert Reservation.objects.get(user__student_no="userC").status == "force_returned"


def test_2b_seated_without_qr_checkin(clock, manager, uc):
    """userC가 예약하고 QR 체크인 없이 앉음 → 착석(체크인 전) → 1분 뒤 체크인 누락(무단 점유) → QR 체크인하면 정상."""
    setup_scenario("2b", clock())
    b2 = seat_no("B-2")
    s = admin_seat(manager, b2)
    assert s["detail"] == "seated_unchecked" and s["category"] == "normal" and s["next_label"] == "체크인 누락"
    assert "QR로 체크인" in uc.jget("/api/seats")["my_status"]["message"]
    clock.advance(60)
    s = admin_seat(manager, b2)
    assert (s["detail"], s["category"]) == ("no_checkin", "unauthorized") and s["needs_action"]
    assert Notification.objects.filter(user__student_no="userC", title__contains="체크인해 주세요").exists()
    rid = Reservation.objects.get(user__student_no="userC").id
    assert uc.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(b2)}).status_code == 200
    s = admin_seat(manager, b2)
    assert s["detail"] == "using" and not s["needs_action"]
    assert not Alert.objects.filter(seat_id=b2, resolved_at__isnull=True).exists()  # 처리 필요 알림 자동 해소


def test_3_reserver_sits_in_wrong_seat(clock, manager, ua):
    """userA가 A-1을 예약하고 B-1에 착각해 앉음 → (본인) B-1 QR이 'A-1이 예약 좌석'이라고 안내 → 관리자는 예약을 B-1로 옮기고 체크인."""
    setup_scenario("3", clock())
    a1, b1 = seat_no("A-1"), seat_no("B-1")
    assert admin_seat(manager, a1)["detail"] == "waiting"
    assert admin_seat(manager, b1)["detail"] == "detected"
    d = ua.jget(f"/api/seats/{b1}?t={qr_token(b1)}")
    assert d["page_mode"] == "reserve_now" and d["my_reservation"]["seat_label"] == "A-1" and d["qr_ok"]
    assert err(ua.jpost(f"/api/reservations/{d['my_reservation']['id']}/checkin", {"qr_token": qr_token(b1)})) == "BAD_QR_TOKEN"
    clock.advance(60)
    assert admin_seat(manager, b1)["detail"] == "unauthorized"  # 1분 뒤에는 B-1이 무단 점유로 보인다
    rid = d["my_reservation"]["id"]
    assert manager.jpost(f"/api/admin/reservations/{rid}/move", {"seat_no": b1, "seated": True}).status_code == 200
    r = Reservation.objects.get(id=rid)
    assert r.seat_id == b1 and r.status == "in_use" and r.checked_in_at == clock()
    assert admin_seat(manager, b1)["detail"] == "using"
    assert admin_seat(manager, a1)["detail"] == "empty"
    assert not Alert.objects.filter(resolved_at__isnull=True).exclude(type="call").exists()


def test_3_self_service_switch(clock, ua):
    """관리자 없이: userA가 B-1 QR로 'A-1 → B-1 바꾸기' (A-1 예약 취소 후 B-1 예약·체크인)."""
    setup_scenario("3", clock())
    a1, b1 = seat_no("A-1"), seat_no("B-1")
    rid = Reservation.objects.get(user__student_no="userA").id
    assert ua.jpost(f"/api/reservations/{rid}/return").status_code == 200
    r = ua.jpost("/api/reservations", {"seat_no": b1, "qr_token": qr_token(b1)}).json()
    assert Reservation.objects.get(id=r["id"]).status == "in_use"


def test_move_seated_rejects_reserved_or_unavailable_target(clock, manager):
    setup_scenario("3", clock())
    rid = Reservation.objects.get(user__student_no="userA").id
    Seat.objects.filter(label="B-1").update(state="unavailable", reason="broken")
    r = manager.jpost(f"/api/admin/reservations/{rid}/move", {"seat_no": seat_no("B-1"), "seated": True})
    assert err(r) == "SEAT_UNAVAILABLE"


def test_scenario_command(clock):
    from django.core.management import call_command
    call_command("scenario", "2b", "--demo-settings")
    assert Reservation.objects.get(user__student_no="userC").status == "reserved"
    call_command("scenario", "reset")
    assert not Reservation.objects.filter(status__in=("reserved", "in_use")).exists()
