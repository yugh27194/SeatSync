"""판정 갱신 파이프라인과 예약·좌석·관리자 공통 로직."""
from django.db import transaction

from .models import AdminLog, Alert, Reservation, Seat, SeatState, Setting, StatusLog, User
from .status import (DEFAULT_SETTINGS, DETAILS, ISSUES, OK, Actual, Settings, judge)
from .status import Reservation as ResView
from .timeutil import to_iso

ACTIVE = ("reserved", "in_use")


# ---------------------------------------------------------------- 조회 헬퍼

def get_settings():
    return Settings.from_dict(dict(Setting.objects.values_list("key", "value")))


def active_reservations():
    """seat_no → 활성 예약(예약자 포함)."""
    return {r.seat_id: r for r in Reservation.objects.filter(status__in=ACTIVE).select_related("user", "seat")}


def user_active_reservation(user_id):
    return Reservation.objects.filter(user_id=user_id, status__in=ACTIVE).select_related("seat").first()


def to_res(r):
    if r is None:
        return None
    return ResView(id=r.id, status=r.status, start_at=r.start_at, end_at=r.end_at, checked_in_at=r.checked_in_at)


def to_actual(seat):
    return Actual(state=seat.state, since=seat.state_since, mark=seat.mark, reason=seat.reason)


# ---------------------------------------------------------------- 파이프라인

def clear_marks(seat_nos):
    """예약이 끝난 좌석의 관리자 지정 의도(mark)를 지운다.
    예약 중 '짐만 있음'으로 확인해 둔 짐이 반납 뒤에도 정상으로 남지 않게 한다."""
    Seat.objects.filter(no__in=list(seat_nos), mark__isnull=False).update(mark=None)


def _sweep(now, s):
    limit = s.checkin_limit_min * 60
    ended = set()
    for r in Reservation.objects.filter(status="reserved", start_at__lt=now - limit):
        r.status, r.ended_at = "no_show", now
        r.save(update_fields=["status", "ended_at"])
        ended.add(r.seat_id)
        if not Alert.objects.filter(seat_id=r.seat_id, type="no_show", resolved_at__isnull=True).exists():
            Alert.objects.create(seat_id=r.seat_id, type="no_show", reservation=r, created_at=now)
    expired = Reservation.objects.filter(status__in=ACTIVE, end_at__lte=now)
    ended.update(expired.values_list("seat_id", flat=True))
    expired.update(status="expired", ended_at=now)
    if ended:
        clear_marks(ended)


def _judge_seats(now, s):
    res_map = active_reservations()
    out = []
    for seat in Seat.objects.filter(active=True):
        res = res_map.get(seat.no)
        out.append({"seat": seat, "res": res, "j": judge(to_res(res), to_actual(seat), now, s)})
    return out


def _record(item, now):
    """세부 상태 전이 기록 + 처리 필요 알림 생성/자동 해소."""
    seat, j, res = item["seat"], item["j"], item["res"]
    prev = SeatState.objects.filter(seat=seat).first()
    prev_detail = prev.detail if prev else None
    if prev_detail != j.detail:
        StatusLog.objects.create(seat_no=seat.no, seat_state=j.seat_state, detail=j.detail, prev_detail=prev_detail,
                                 reservation_id=res.id if res else None, actual=seat.state, at=now)
        SeatState.objects.update_or_create(seat=seat, defaults={"seat_state": j.seat_state, "detail": j.detail,
                                                                "since": j.since})
        # 알림은 처리 필요 상태로 '전이'할 때만 만든다 — 처리 완료 후 같은 상태가 이어져도 다시 뜨지 않게.
        if j.needs_action and not Alert.objects.filter(seat=seat, type=j.detail, resolved_at__isnull=True).exists():
            Alert.objects.create(seat=seat, type=j.detail, reservation=res, created_at=now)
    stale = [t for t in ISSUES if t != j.detail]
    Alert.objects.filter(seat=seat, resolved_at__isnull=True, type__in=stale).update(resolved_at=now, resolution="auto")


def refresh(now):
    """sweep → 전 좌석 판정·대조 → 전이 기록 → 알림. 좌석별 결과 목록을 돌려준다."""
    with transaction.atomic():
        s = get_settings()
        _sweep(now, s)
        results = _judge_seats(now, s)
        for item in results:
            _record(item, now)
    return results


# ---------------------------------------------------------------- 좌석 현장 상태

def set_seat_state(seat, state, source, now, mark=None, reason=None, note=None):
    """현장 상태 변경. 같은 상태면 시작 시각을 유지한다. mark·사유는 매번 새 값으로 덮어쓴다."""
    if state != "unavailable":
        reason = None
    if seat.state != state:
        seat.state_since = now
    seat.state, seat.mark, seat.reason, seat.note, seat.state_source = state, mark, reason, note, source
    seat.save(update_fields=["state", "state_since", "mark", "reason", "note", "state_source"])


# ---------------------------------------------------------------- 예약 공통

def extend_check(res, now, s):
    """(연장 가능 여부, 불가 사유)."""
    if res is None or res.status != "in_use":
        return False, "체크인한 뒤 이용 중일 때만 연장할 수 있어요."
    if res.extend_count >= s.max_extends:
        return False, f"연장은 최대 {s.max_extends}회까지 가능해요."
    if res.end_at - now > s.extend_window_min * 60:
        return False, f"종료 {s.extend_window_min}분 전부터 연장할 수 있어요."
    return True, None


def reservation_json(res, now, s):
    if res is None:
        return None
    ok, reason = extend_check(res, now, s)
    return {
        "id": res.id,
        "seat_no": res.seat_id,
        "seat_label": res.seat.label,
        "status": res.status,
        "start_at": to_iso(res.start_at),
        "end_at": to_iso(res.end_at),
        "checked_in_at": to_iso(res.checked_in_at),
        "checkin_deadline": to_iso(res.start_at + s.checkin_limit_min * 60) if res.status == "reserved" else None,
        "can_extend": ok,
        "extend_reason": reason,
        "extend_count": res.extend_count,
        "max_extends": s.max_extends,
        "remaining_sec": max(0, res.end_at - now),
    }


# ---------------------------------------------------------------- 관리자 이력

def log_admin(admin_id, action, now, seat_no=None, reservation_id=None, target_user_id=None, alert_id=None, memo=None):
    AdminLog.objects.create(admin_id=admin_id, action=action, seat_id=seat_no, reservation_id=reservation_id,
                            target_user_id=target_user_id, alert_id=alert_id, memo=memo, at=now)


# ---------------------------------------------------------------- 통계

def _bucket(seat_state, detail):
    if detail in ("away", "hoarding", "unauthorized"):
        return detail
    if seat_state == "in_use" and not DETAILS[detail][2]:
        return "in_use"
    return None


def compute_hourly_stats(day_start, day_end, now):
    """StatusLog 전이 이력을 구간으로 펼쳐 시간대별 좌석·분 누적을 계산한다."""
    secs = [{"in_use": 0, "away": 0, "hoarding": 0, "unauthorized": 0} for _ in range(24)]
    end_cap = min(day_end, now)
    for seat_no in StatusLog.objects.values_list("seat_no", flat=True).distinct():
        before = StatusLog.objects.filter(seat_no=seat_no, at__lt=day_start).order_by("-at", "-id").first()
        logs = StatusLog.objects.filter(seat_no=seat_no, at__gte=day_start, at__lt=end_cap).order_by("at", "id")
        points = ([(before.seat_state, before.detail, day_start)] if before else []) + \
                 [(r.seat_state, r.detail, r.at) for r in logs]
        for i, (st, det, t0) in enumerate(points):
            t1 = points[i + 1][2] if i + 1 < len(points) else end_cap
            key = _bucket(st, det)
            t = t0
            while key and t < t1:
                h = (t - day_start) // 3600
                seg = min(t1, day_start + (h + 1) * 3600) - t
                if 0 <= h < 24:
                    secs[h][key] += seg
                t += seg
    hours = []
    for h, x in enumerate(secs):
        base = x["in_use"] + x["away"] + x["hoarding"]
        hours.append({
            "hour": h,
            "in_use_min": round(x["in_use"] / 60, 1),
            "away_min": round(x["away"] / 60, 1),
            "hoarding_min": round(x["hoarding"] / 60, 1),
            "unauthorized_min": round(x["unauthorized"] / 60, 1),
            # 이탈·사석화 비율 = (이탈 + 사석화) / (정상 이용 + 이탈 + 사석화)
            "issue_rate": round((x["away"] + x["hoarding"]) / base, 3) if base else 0.0,
        })
    return hours


# ---------------------------------------------------------------- 시연 상황 배치

# (좌석 라벨, 예약자 아이디, 예약 상태, 예약 시작(분 전; None=체크인 제한 초과), 현장 상태, mark, 사유, 현장 시작(분 전), 설명)
DEMO_LAYOUT = [
    ("A-1", "userA",    "in_use",   30, "occupied",    None,  None,          30, "정상 이용"),
    ("A-2", "userB",    "in_use",   50, "empty",       None,  None,          40, "이탈"),
    ("A-3", None,       None,        0, "occupied",    None,  None,          10, "무단 점유"),
    ("A-4", "userC",    "reserved",  5, "occupied",    None,  None,           3, "체크인 누락"),
    ("A-5", None,       None,        0, "occupied",    "ok",  None,          20, "이용 중(관리자 확인, 예약 없음)"),
    ("B-1", "20260001", "reserved",  3, "unavailable", None,  "broken",       1, "예약 좌석 사용불가"),
    ("B-2", "20260002", "in_use",   60, "item",        None,  None,          40, "사석화"),
    ("B-3", "20260003", "reserved", None, "empty",     None,  None,          30, "미입실 → 자동 취소"),
    ("B-4", "20260004", "in_use",   40, "item",        None,  None,           5, "짐만 있음(잠시 자리 비움)"),
    ("C-1", "20260005", "reserved",  3, "empty",       None,  None,          30, "입실 대기"),
    ("C-2", None,       None,        0, "item",        None,  None,          15, "무단 점유(짐으로 자리 맡기)"),
    ("D-1", None,       None,        0, "unavailable", None,  "maintenance", 30, "점검·청소"),
    ("E-3", None,       None,        0, "unavailable", None,  "broken",      90, "고장(콘센트)"),
]
DEMO_NOTES = {"B-1": "의자 파손", "D-1": "청소 중", "E-3": "콘센트 고장"}


def setup_demo(now):
    """활성 예약·미해결 알림을 정리하고 DEMO_LAYOUT대로 다양한 상황을 만든다. 안내 문구 목록을 돌려준다."""
    msgs = []
    with transaction.atomic():
        s = get_settings()
        Reservation.objects.filter(status__in=ACTIVE).update(status="cancelled", ended_at=now)
        Alert.objects.filter(resolved_at__isnull=True).update(resolved_at=now, resolution="reset")
        SeatState.objects.all().delete()  # 같은 상태라도 알림이 새로 생기게 캐시를 비운다
        seats = {st.label: st for st in Seat.objects.filter(active=True)}
        # 목록에 없는 좌석은 빈자리로
        listed = {row[0] for row in DEMO_LAYOUT}
        Seat.objects.filter(active=True).exclude(label__in=listed).update(
            state="empty", mark=None, reason=None, note=None, state_since=now, state_source="manual")
        for label, sno, status, start_ago, state, mark, reason, state_ago, memo in DEMO_LAYOUT:
            seat = seats.get(label)
            if seat is None:
                continue
            seat.state, seat.mark, seat.reason = state, mark, reason
            seat.note, seat.state_since, seat.state_source = DEMO_NOTES.get(label), now - state_ago * 60, "manual"
            seat.save()
            who = "예약 없음"
            if sno:
                user = User.objects.filter(student_no=sno).first()
                if user is None:
                    continue
                if start_ago is None:  # 체크인 제한을 넘긴 예약 → 다음 refresh에서 미입실 처리
                    start_ago = s.checkin_limit_min + 5
                start = now - start_ago * 60
                Reservation.objects.create(user=user, seat=seat, status=status, start_at=start,
                                           end_at=start + s.default_use_min * 60,
                                           checked_in_at=start if status == "in_use" else None)
                who = f"{user.name} {'이용 중' if status == 'in_use' else '예약'}"
            msgs.append(f"{label}: {who} → {memo}")
        refresh(now)
    return msgs


def default_setting_rows():
    return [Setting(key=k, value=str(v)) for k, v in DEFAULT_SETTINGS.items()]


