"""추가 기능: 내 이용 기록, 혼잡도, 빈자리 알림 대기, 판정 피드백, 사전 경고."""
from conftest import ApiClient, admin_seat, err, login, qr_token, set_state, user_id

from seats.models import Notification, ReservationEvent, WaitEntry


def reserve(c, no, token=None):
    return c.jpost("/api/reservations", {"seat_no": no, **({"qr_token": token} if token else {})})


def notes(student_no, kind=None):
    qs = Notification.objects.filter(user__student_no=student_no).order_by("id")
    if kind:
        qs = qs.filter(kind=kind)
    return list(qs.values_list("title", flat=True))


# ================================================================ 1. 내 이용 기록

def test_events_recorded_through_lifecycle(user, clock):
    rid = reserve(user, 1).json()["id"]
    clock.advance(120)
    user.jpost(f"/api/reservations/{rid}/checkin", {"qr_token": qr_token(1)})
    clock.advance(95 * 60)
    user.jpost(f"/api/reservations/{rid}/extend")
    clock.advance(10 * 60)
    user.jpost(f"/api/reservations/{rid}/return")
    kinds = list(ReservationEvent.objects.filter(reservation_id=rid).order_by("id").values_list("kind", flat=True))
    assert kinds == ["reserve", "checkin", "extend", "return"]
    r = user.jget("/api/me/reservations")["reservations"][0]
    assert r["used_min"] == 105 and r["extend_count"] == 1 and r["status_label"] == "반납"
    assert [e["label"] for e in r["events"]] == ["예약", "체크인", "연장", "반납"]
    assert "→" in r["events"][2]["memo"]


def test_history_buckets_and_summary(user, clock):
    rid = reserve(user, 1, qr_token(1)).json()["id"]
    clock.advance(90 * 60)
    user.jpost(f"/api/reservations/{rid}/return")
    d = user.jget("/api/me/history?period=day")
    # 테스트 시각이 23:13이라 90분이 자정을 넘어 이틀에 나뉜다
    assert len(d["buckets"]) == 14 and d["buckets"][-2]["minutes"] + d["buckets"][-1]["minutes"] == 90
    assert d["summary"]["total_min"] == 90 and d["summary"]["returns"] == 1 and d["summary"]["reservations"] == 1
    assert len(user.jget("/api/me/history?period=week")["buckets"]) == 8
    m = user.jget("/api/me/history?period=month")
    assert len(m["buckets"]) == 6 and m["buckets"][-1]["minutes"] == 90


def test_history_splits_across_midnight(user, clock):
    from datetime import datetime, timedelta
    from seats.timeutil import tz
    midnight = int((datetime.fromtimestamp(clock(), tz()).replace(hour=0, minute=0, second=0) + timedelta(days=1)).timestamp())
    clock.t = midnight - 30 * 60
    rid = reserve(user, 1, qr_token(1)).json()["id"]
    clock.t = midnight + 45 * 60
    user.jpost(f"/api/reservations/{rid}/return")
    b = user.jget("/api/me/history?period=day")["buckets"]
    assert (b[-2]["minutes"], b[-1]["minutes"]) == (30, 45)


def test_no_show_counted(user, clock):
    reserve(user, 1)
    clock.advance(121 * 60)  # 예약 끝까지 체크인하지 않음
    s = user.jget("/api/me/history")["summary"]
    assert s["no_shows"] == 1 and s["total_min"] == 0


# ================================================================ 2. 혼잡도

def test_congestion_user_vs_admin(user, admin, clock):
    reserve(user, 1, qr_token(1))
    clock.advance(50 * 60)  # 관리자 모드는 60분 무사용 시 꺼지므로 그 전에 확인
    d = user.jget("/api/congestion")
    assert d["days"][0] == "월" and d["hours"][0] == 6 and d["hours"][-1] == 23
    assert "actual" not in d and "idle" not in d and "actual_rate" not in d["live"]
    a = admin.jget("/api/congestion")
    assert {"occupancy", "actual", "idle", "issue"} <= a.keys() and "actual_rate" in a["live"]
    assert a["live"]["in_use"] == 3  # A-1 예약 + 초기 배분 A-3(무단 점유)·A-5(이용 중)


def test_congestion_measures_actual_use(admin, clock):
    from seats.analytics import congestion
    from seats.services import get_settings
    from seats.models import StatusLog
    from datetime import datetime
    from seats.timeutil import tz
    t0 = int(datetime.fromtimestamp(clock(), tz()).replace(minute=0, second=0).timestamp()) - 7 * 86400
    StatusLog.objects.filter(seat_no=1).delete()
    StatusLog.objects.create(seat_no=1, seat_state="in_use", detail="using", at=t0)
    StatusLog.objects.create(seat_no=1, seat_state="in_use", detail="item", at=t0 + 1800)   # 30분 실사용, 30분 짐만
    StatusLog.objects.create(seat_no=1, seat_state="available", detail="empty", at=t0 + 3600)
    d = congestion(clock(), get_settings(), weeks=4)
    wd, h = datetime.fromtimestamp(t0, tz()).weekday(), datetime.fromtimestamp(t0, tz()).hour
    if h in d["hours"]:
        i = d["hours"].index(h)
        # 해당 요일·시각은 4주 중 한 번, 20석 → 1좌석·1시간 = 1/80, 그중 절반만 실사용
        assert abs(d["occupancy"][wd][i] - d["actual"][wd][i] * 2) < 0.02
        assert d["idle"][wd][i] > 0


# ================================================================ 3. 빈자리 알림 대기

def _fill_all_free(c_list):
    """빈자리를 모두 채운다(테스트용 예약)."""
    from seats.models import Reservation, Seat, User
    from seats import clock as ck
    now = ck.now()
    others = list(User.objects.filter(student_no__startswith="fill"))
    free = [s.no for s in Seat.objects.filter(active=True, state="empty") if not Reservation.objects.filter(seat=s, status__in=("reserved", "in_use")).exists()]
    for i, no in enumerate(free):
        u = User.objects.create(student_no=f"fill{i}", name=f"채움{i}", password="x", created_at=now) if i >= len(others) else others[i]
        Reservation.objects.create(user=u, seat_id=no, status="in_use", start_at=now, end_at=now + 7200, checked_in_at=now)
    return free


def test_waitlist_offer_hold_and_fulfill(user_a, user_b, admin, clock):
    free = _fill_all_free(None)
    r = user_a.jpost("/api/waitlist", {})
    assert r.status_code == 201 and r.json()["waitlist"]["position"] == 1
    assert user_b.jpost("/api/waitlist", {}).json()["waitlist"]["position"] == 2
    assert err(user_a.jpost("/api/waitlist", {})) == "ALREADY_WAITING"
    # 빈자리 발생
    from seats.models import ACTIVE, Reservation
    res = Reservation.objects.get(seat_id=free[0], status="in_use")
    res.status, res.ended_at = "returned", clock()
    res.save()
    d = user_a.jget("/api/seats")
    assert d["waitlist"]["status"] == "offered" and d["waitlist"]["offer"]["seat_no"] == free[0]
    assert next(s for s in d["seats"] if s["no"] == free[0])["view"] == "offered"
    assert next(s for s in user_b.jget("/api/seats")["seats"] if s["no"] == free[0])["view"] == "held"
    assert "빈자리가 생겼어요" in notes("userA", "offer")[0]
    # 다른 사람은 예약 불가, 안내받은 사람은 가능
    assert err(reserve(user_b, free[0])) == "SEAT_HELD"
    assert reserve(user_a, free[0]).status_code == 201
    assert WaitEntry.objects.get(user__student_no="userA").status == "fulfilled"
    assert user_b.jget("/api/seats")["waitlist"]["position"] == 1


def test_waitlist_expiry_passes_to_next(user_a, user_b, clock):
    free = _fill_all_free(None)
    user_a.jpost("/api/waitlist", {})
    clock.advance(1)
    user_b.jpost("/api/waitlist", {})
    from seats.models import ACTIVE, Reservation
    Reservation.objects.filter(seat_id=free[0]).update(status="returned", ended_at=clock())
    user_a.get("/api/seats")
    clock.advance(5 * 60)  # 안내 유지 5분 경과
    d = user_b.jget("/api/seats")
    assert d["waitlist"]["status"] == "offered"
    assert WaitEntry.objects.get(user__student_no="userA").status == "expired"
    assert any("시간이 지났어요" in t for t in notes("userA", "offer"))


def test_waitlist_decline_and_zone(user_a, user_b, clock):
    free = _fill_all_free(None)
    user_a.jpost("/api/waitlist", {"zone": "집중석"})
    user_b.jpost("/api/waitlist", {})
    from seats.models import Reservation, Seat
    window = next(no for no in free if Seat.objects.get(no=no).zone == "창가석")
    Reservation.objects.filter(seat_id=window).update(status="returned", ended_at=clock())
    user_a.get("/api/seats")
    assert WaitEntry.objects.get(user__student_no="userA").status == "waiting"   # 창가석은 원하는 구역이 아님
    assert WaitEntry.objects.get(user__student_no="userB").offered_seat_id == window
    assert user_b.jpost("/api/waitlist/decline").status_code == 200
    assert WaitEntry.objects.get(user__student_no="userB").status == "declined"


def test_waitlist_rules(user, admin):
    assert user.jpost("/api/waitlist", {"zone": "없는 구역"}).status_code == 400
    reserve(user, 1)
    assert err(user.jpost("/api/waitlist", {})) == "ALREADY_HAS_RESERVATION"
    assert err(user.jpost("/api/waitlist/cancel")) == "INVALID_STATE"


def test_waitlist_immediate_offer_when_free(user_a):
    d = user_a.jpost("/api/waitlist", {"zone": "창가석"}).json()["waitlist"]
    assert d["status"] == "offered" and d["offer"]["seat_label"].startswith("A-")


# ================================================================ 4. 판정 피드백

def test_feedback_correct_and_wrong(admin, device, clock):
    from conftest import send
    send(device, clock, {1: ("item", clock())})  # 카메라가 짐으로 판정(실제로는 사람)
    assert admin.jpost("/api/admin/seats/2/feedback", {"verdict": "correct"}).status_code == 200
    assert admin.jpost("/api/admin/seats/1/feedback", {"verdict": "wrong"}).status_code == 400
    r = admin.jpost("/api/admin/seats/1/feedback", {"verdict": "wrong", "correct_detail": "using", "memo": "사람이 있음"})
    assert r.status_code == 200
    assert admin_seat(admin, 1)["detail"] == "using"  # 바로 반영
    st = admin.jget("/api/admin/feedback")
    assert st["overall"] == {"total": 2, "correct": 1, "accuracy": 0.5}
    assert st["by_source"]["camera"]["accuracy"] == 0.0
    assert st["confusion"][0] == {"shown": "짐만 있음", "correct": "정상 이용", "count": 1}
    assert st["recent"][0]["applied"] is True


def test_feedback_record_only(admin):
    admin.jpost("/api/admin/seats/1/feedback", {"verdict": "wrong", "correct_detail": "broken", "apply": False})
    assert admin_seat(admin, 1)["detail"] == "empty"
    assert admin.jpost("/api/admin/seats/1/feedback", {"verdict": "wrong", "correct_detail": "away"}).status_code == 409
    assert ApiClient().post("/api/admin/seats/1/feedback").status_code in (401, 403)


# ================================================================ 5. 사전 경고

def test_prewarn_before_away(user, admin, clock):
    reserve(user, 4, qr_token(4))
    set_state(admin, 4, "empty")
    clock.advance(19 * 60)
    user.get("/api/seats")
    assert notes("20260001", "prewarn") == []
    clock.advance(60)  # 장기 이석 기준(30분) 10분 전
    d = user.jget("/api/seats")
    assert notes("20260001", "prewarn") == ["A-4 좌석 사전 경고"]
    assert d["my_status"]["detail"] == "away_short" and "장기 이석" in d["my_status"]["message"]
    for _ in range(3):
        clock.advance(60)
        user.get("/api/seats")
    assert len(notes("20260001", "prewarn")) == 1  # 같은 사안은 한 번만
    clock.advance(10 * 60)
    user.get("/api/seats")
    assert notes("20260001", "issue") == ["A-4 좌석이 '장기 이석'(으)로 표시됐어요"]


def test_prewarn_hoarding_and_new_episode(user, admin, clock):
    reserve(user, 4, qr_token(4))
    set_state(admin, 4, "item")
    clock.advance(21 * 60)
    user.get("/api/seats")
    set_state(admin, 4, "using")   # 돌아옴
    clock.advance(60)
    set_state(admin, 4, "item")    # 다시 자리 비움 → 새 사안
    clock.advance(21 * 60)
    user.get("/api/seats")
    assert len(notes("20260001", "prewarn")) == 2


def test_prewarn_checkin_deadline(user, clock):
    reserve(user, 1)
    clock.advance(6 * 60)
    user.get("/api/seats")
    assert notes("20260001", "prewarn") == ["A-1 체크인 마감 9분 전"]
    clock.advance(10 * 60)
    user.get("/api/seats")
    assert "A-1 체크인 시간이 지났어요" in notes("20260001", "issue")


def test_admin_actions_notify_user(user, admin):
    uid = user_id("20260001")
    rid = reserve(user, 1, qr_token(1)).json()["id"]
    admin.jpost(f"/api/admin/users/{uid}/notice", {"message": "자리를 오래 비우셨어요", "seat_no": 1})
    admin.jpost(f"/api/admin/users/{uid}/warn", {"reason": "사석화"})
    admin.jpost(f"/api/admin/reservations/{rid}/force-return", {"memo": "사석화 40분"})
    admin.jpost(f"/api/admin/users/{uid}/suspend", {"days": 3})
    titles = notes("20260001")
    assert "A-1 좌석 관련 사전 경고" in titles and "경고가 부여됐어요 (누적 1회)" in titles
    assert "A-1 예약이 관리자에 의해 반납됐어요" in titles and "이용이 3일 정지됐어요" in titles
    # 사전 경고는 누적 경고에 포함되지 않음
    from seats.models import User
    assert User.objects.get(id=uid).warnings == 1


def test_notifications_api_and_read(user, admin):
    admin.jpost(f"/api/admin/users/{user_id('20260001')}/notice", {"message": "주의"})
    d = user.jget("/api/me/notifications")
    assert d["unread"] == 1 and d["items"][0]["kind"] == "prewarn"
    assert user.jpost("/api/me/notifications/read", {"ids": [d["items"][0]["id"]]}).json()["read"] == 1
    assert user.jget("/api/me/notifications")["unread"] == 0
    other = login(ApiClient(), "userC")
    assert other.jget("/api/me/notifications")["items"] == []  # 다른 사람 알림은 보이지 않음


def test_pages_render(user, admin):
    for path in ("/history", "/congestion", "/map", "/my"):
        r = user.get(path)
        assert r.status_code == 200 and 'class="tabs"' in r.content.decode()
    assert 'class="admin-tab' in admin.get("/map").content.decode()


def test_sample_history_generator(admin, clock):
    r = admin.jpost("/api/admin/demo-history", {"weeks": 1})
    assert r.status_code == 200 and r.json()["reservations"] > 50
    d = login(ApiClient(), "userA").jget("/api/me/history?period=day")
    assert d["summary"]["total_min"] > 0
    c = admin.jget("/api/congestion")
    assert c["peak"] is not None and c["peak"]["rate"] > 0
    assert admin.jpost("/api/admin/demo-history", {"weeks": 20}).status_code == 400


def test_sample_history_with_active_reservations(admin, clock):
    # 지금 예약 중인 좌석·사용자가 있어도 샘플 이력 생성이 실패하지 않아야 한다
    from seats.models import ACTIVE, Reservation
    assert admin.jpost("/api/admin/demo", {}).status_code == 200
    assert Reservation.objects.filter(status__in=ACTIVE).exists()
    r = admin.jpost("/api/admin/demo-history", {"weeks": 4})
    assert r.status_code == 200 and r.json()["reservations"] > 50
