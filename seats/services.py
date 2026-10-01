"""판정 갱신 파이프라인과 예약·좌석·관리자 공통 로직."""
from django.db import transaction


from .models import (AdminLog, Alert, Notification, Reservation, ReservationEvent, Seat, SeatState, Setting,
                     StatusLog, User, WaitEntry)
from .status import (AVAILABLE, DEFAULT_SETTINGS, DETAILS, ISSUES, OK, Actual, Settings, fmt_min, fmt_sec, judge)
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


def camera_stale_since(seat, now):
    """카메라가 판정한 좌석의 감지를 믿을 수 없게 된 시각. 수동 지정·QR 체크인 등 카메라가 아닌 상태면 None."""
    if seat.state_source != "camera":
        return None
    if seat.cam_state == "UNKNOWN":
        return seat.cam_unknown_since or now
    if seat.cam_valid_until is not None and now > seat.cam_valid_until:
        return seat.cam_valid_until
    return None


# ---------------------------------------------------------------- 짐 감지 (관리자 탭 > 판정 기준 상단 on/off)

ITEM_DETECTION_KEY = "item_detection"


def item_detection_on():
    """짐 감지 사용 여부. 기본은 꺼짐 — 켜면 카메라의 has_item으로 '짐만 있음'을 판정하고 지도에 노란 점을 찍는다."""
    v = Setting.objects.filter(key=ITEM_DETECTION_KEY).values_list("value", flat=True).first()
    return v == "1"


def set_item_detection(enabled, now):
    """짐 감지를 켜고 끈다. 끌 때는 카메라가 정한 '짐만 있음'을 '비어 있음'으로 되돌린다
    (사람 감지만으로 판정하던 원래 방식으로. 관리자가 직접 지정한 '짐만 있음'은 그대로 둔다).
    시작 시각은 유지해 체크인한 사람의 이석 시간이 처음부터 다시 세어지지 않게 한다."""
    Setting.objects.update_or_create(key=ITEM_DETECTION_KEY, defaults={"value": "1" if enabled else "0"})
    if not enabled:
        Seat.objects.filter(active=True, state="item", state_source="camera").update(state="empty", mark=None)


def seat_has_item(seat, on, now):
    """관리자 지도에 노란 점(짐 있음)을 찍을지. 짐 감지가 켜져 있을 때만.
    사람 유무와 상관없이 짐 유무만 본다: 카메라의 짐 판정(has_item)이 유효하면 그 값을 그대로 쓰고,
    짐을 모를 때(카메라 끊김·짐 감지 없는 카메라)만 마지막 '짐만 있음' 상태(관리자 지정 포함)를 따른다."""
    if not on:
        return False
    if seat.cam_item is not None and (seat.cam_valid_until or 0) >= now:
        return seat.cam_item
    return seat.state == "item"


def to_actual(seat, now):
    return Actual(state=seat.state, since=seat.state_since, mark=seat.mark, reason=seat.reason,
                  stale_since=camera_stale_since(seat, now))


# ---------------------------------------------------------------- 파이프라인

def clear_marks(seat_nos, now=None):
    """예약이 끝난 좌석의 관리자 지정 의도(mark)를 지운다.
    예약 중 '짐만 있음'으로 확인해 둔 짐이 반납 뒤에도 정상으로 남지 않게 한다.
    now를 주면 아직 사람(짐)이 있는 좌석의 시작 시각을 예약 종료 시점으로 맞춘다 — 방금까지 예약해 쓰던 사람이
    곧바로 무단 점유가 되지 않고, 무단 점유 기준 시간만큼 정리할 시간을 갖게 한다."""
    Seat.objects.filter(no__in=list(seat_nos), mark__isnull=False).update(mark=None)
    if now is not None:
        Seat.objects.filter(no__in=list(seat_nos), state__in=("occupied", "item")).update(state_since=now)


def _sweep(now, s):
    # 체크인 제한 시간이 지난 예약은 자동 취소하지 않는다 — 판정에서 '미입실'(확인 필요)로 표시하고 관리자가 판단한다.
    # 예약 종료 시각까지 끝내 체크인하지 않으면 그때 미입실로 끝낸다.
    ended = set()
    for r in Reservation.objects.filter(status__in=ACTIVE, end_at__lte=now).select_related("seat"):
        no_show = r.status == "reserved"
        r.status, r.ended_at = ("no_show" if no_show else "expired"), now
        r.save(update_fields=["status", "ended_at"])
        ended.add(r.seat_id)
        record_event(r, "no_show" if no_show else "expire", r.end_at)
        if no_show:
            notify(r.user_id, "issue", "danger", f"{r.seat.label} 예약이 미입실로 끝났어요",
                   "예약 시간 안에 체크인하지 않았어요.", now, seat=r.seat, reservation=r, key=f"noshow:{r.id}")
    if ended:
        clear_marks(ended, now)


def _judge_seats(now, s):
    res_map = active_reservations()
    out = []
    for seat in Seat.objects.filter(active=True):
        res = res_map.get(seat.no)
        out.append({"seat": seat, "res": res, "j": judge(to_res(res), to_actual(seat, now), now, s)})
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


AUTO_RETURN = ("away", "hoarding", "no_show")


def _auto_return(results, now, s):
    """미입실·장기 이석·사석화가 auto_return_min 넘게 이어지면 예약을 자동으로 강제 반납(미입실은 취소)한다.
    카메라 감지가 끊긴 좌석은 자리 비움을 확신할 수 없으므로 자동 반납하지 않는다."""
    limit = s.sec("auto_return_min")
    if limit <= 0:
        return
    for it in results:
        r, j, seat = it["res"], it["j"], it["seat"]
        if r is None or j.detail not in AUTO_RETURN or j.stale or now - j.since < limit:
            continue
        no_show = r.status == "reserved"
        label = DETAILS[j.detail][1]
        r.status, r.ended_at = ("no_show" if no_show else "force_returned"), now
        r.save(update_fields=["status", "ended_at"])
        memo = f"자동 반납 ({label} {fmt_min(s.auto_return_min)} 경과)"
        record_event(r, "no_show" if no_show else "force_return", now, memo=memo)
        Alert.objects.filter(seat=seat, resolved_at__isnull=True).exclude(type="call").update(
            resolved_at=now, resolution="auto_return")
        clear_marks([seat.no], now)
        seat.refresh_from_db(fields=["mark", "state_since"])
        notify(r.user_id, "issue", "danger",
               f"{seat.label} 예약이 자동으로 {'취소' if no_show else '반납'}됐어요",
               f"'{label}' 상태가 {fmt_min(s.auto_return_min)} 동안 이어졌어요.", now, seat=seat, reservation=r, key=f"auto:{r.id}")
        log_admin(None, "auto_return", now, seat_no=seat.no, reservation_id=r.id, target_user_id=r.user_id, memo=memo)
        it["res"] = None
        it["j"] = judge(None, to_actual(seat, now), now, s)


def refresh(now):
    """sweep → 전 좌석 판정·대조 → 전이 기록 → 처리 필요 알림 → 본인 사전 경고 → 빈자리 안내.
    좌석별 결과 목록을 돌려준다(item["offer"]: 그 좌석을 안내받은 대기 항목)."""
    with transaction.atomic():
        s = get_settings()
        _sweep(now, s)
        results = _judge_seats(now, s)
        _auto_return(results, now, s)
        for item in results:
            _record(item, now)
        _prewarn(results, now, s)
        offers = _waitlist(results, now, s)
        for item in results:
            item["offer"] = offers.get(item["seat"].no)
    return results


# ---------------------------------------------------------------- 예약 이력·본인 알림

def record_event(res, kind, at, memo=None):
    ReservationEvent.objects.create(reservation=res, user_id=res.user_id, kind=kind, at=at, memo=memo)


def notify(user_id, kind, level, title, body, now, seat=None, reservation=None, key=None):
    """본인 계정 알림. key가 같으면 한 번만 보낸다(같은 사안의 중복 경고 방지)."""
    if key and Notification.objects.filter(dedup_key=key).exists():
        return None
    return Notification.objects.create(user_id=user_id, kind=kind, level=level, title=title, body=body,
                                       seat=seat, reservation=reservation, dedup_key=key, created_at=now)


def _prewarn(results, now, s):
    """일시 이석·짐만 두고 비움·체크인 마감이 기준 시간 prewarn_min 전이면 본인에게 사전 경고,
    처리 필요로 넘어가면 다시 알림. 같은 사안(예약·상태 시작 시각)마다 한 번씩만 보낸다.
    카메라 판단 불가(stale)인 동안의 deadline은 '판단 불가' 기준이라 본인 경고에 쓰지 않는다."""
    pw = s.sec("prewarn_min")
    for it in results:
        r, j, seat = it["res"], it["j"], it["seat"]
        if r is None:
            continue
        d, left = j.detail, (j.deadline - now) if j.deadline else None
        uid = r.user_id
        if d in ("away_short", "item") and j.next_detail in ("away", "hoarding") and left is not None and left <= pw:
            target = "장기 이석" if d == "away_short" else "사석화"
            notify(uid, "prewarn", "warn", f"{seat.label} 좌석 사전 경고",
                   f"{fmt_sec(left)} 안에 자리로 돌아와 주세요. 늦으면 '{target}'(으)로 처리돼요.",
                   now, seat=seat, reservation=r, key=f"pre:{d}:{r.id}:{j.since}")
        elif d in ("away", "hoarding"):
            notify(uid, "issue", "danger", f"{seat.label} 좌석이 '{DETAILS[d][1]}'(으)로 표시됐어요",
                   "자리로 돌아가거나 반납해 주세요. 그대로 두면 반납될 수 있어요.",
                   now, seat=seat, reservation=r, key=f"iss:{d}:{r.id}:{j.since}")
        elif d == "unauthorized":  # 내 예약 좌석을 다른 사람이 점유(관리자 확인)
            notify(uid, "issue", "warn", f"{seat.label} 내 예약 좌석에 다른 이용이 확인됐어요",
                   "관리자가 확인하고 있어요.",
                   now, seat=seat, reservation=r, key=f"iss:unauth:{r.id}:{j.since}")
        elif d == "seat_unavailable":
            notify(uid, "issue", "danger", f"{seat.label} 예약 좌석을 사용할 수 없게 됐어요",
                   "관리자에게 문의하거나 반납 후 다른 좌석을 예약해 주세요.",
                   now, seat=seat, reservation=r, key=f"iss:unav:{r.id}:{j.since}")
        if d == "no_checkin":
            notify(uid, "prewarn", "warn", f"{seat.label} 체크인해 주세요",
                   "좌석 QR을 찍어 체크인해 주세요.",
                   now, seat=seat, reservation=r, key=f"iss:nocheckin:{r.id}:{j.since}")
        if d == "waiting" and left is not None and 0 < left <= pw:
            notify(uid, "prewarn", "warn", f"{seat.label} 체크인 마감 {fmt_sec(left)} 전",
                   f"{fmt_sec(left)} 안에 좌석 QR로 체크인해 주세요. 늦으면 예약이 취소될 수 있어요.",
                   now, seat=seat, reservation=r, key=f"pre:checkin:{r.id}")
        if d == "no_show":
            notify(uid, "issue", "danger", f"{seat.label} 체크인 시간이 지났어요",
                   "도착했다면 바로 좌석 QR로 체크인해 주세요. 이용하지 않으면 예약을 취소해 주세요.", now, seat=seat, reservation=r, key=f"iss:noshow:{r.id}")


# ---------------------------------------------------------------- 빈자리 알림 대기

def _waitlist(results, now, s):
    """안내 만료 처리 → 쓸 수 없게 된 안내 되돌리기 → 빈자리를 대기 순서대로 안내. {seat_no: 안내 중인 WaitEntry}"""
    free = {it["seat"].no: it["seat"] for it in results if it["j"].seat_state == AVAILABLE and it["res"] is None}
    active_users = set(Reservation.objects.filter(status__in=ACTIVE).values_list("user_id", flat=True))
    offers = {}
    for e in WaitEntry.objects.filter(status__in=("waiting", "offered")).select_related("offered_seat"):
        if e.user_id in active_users:  # 다른 경로로 이미 예약함
            e.status, e.ended_at = "fulfilled", now
            e.save(update_fields=["status", "ended_at"])
            continue
        if e.status != "offered":
            continue
        if e.expires_at <= now:
            e.status, e.ended_at = "expired", now
            e.save(update_fields=["status", "ended_at"])
            notify(e.user_id, "offer", "info", "빈자리 안내 시간이 지났어요",
                   f"{e.offered_seat.label} 좌석 안내가 끝났어요. 다시 기다리려면 [빈자리 알림]을 눌러 주세요.",
                   now, seat=e.offered_seat, key=f"offer-exp:{e.id}")
        elif e.offered_seat_id not in free:  # 안내한 좌석을 더 쓸 수 없음(관리자 배정·사용불가 등) → 다시 대기
            e.status, e.offered_seat, e.offered_at, e.expires_at = "waiting", None, None, None
            e.save(update_fields=["status", "offered_seat", "offered_at", "expires_at"])
        else:
            offers[e.offered_seat_id] = e
    for e in WaitEntry.objects.filter(status="waiting").order_by("created_at", "id"):
        cand = [no for no, st in sorted(free.items()) if no not in offers and (not e.zone or st.zone == e.zone)]
        if not cand:
            continue
        seat = free[cand[0]]
        e.status, e.offered_seat, e.offered_at, e.expires_at = "offered", seat, now, now + s.sec("waitlist_hold_min")
        e.save(update_fields=["status", "offered_seat", "offered_at", "expires_at"])
        offers[seat.no] = e
        notify(e.user_id, "offer", "ok", f"{seat.label} 빈자리가 생겼어요",
               f"{fmt_min(s.waitlist_hold_min)} 동안 먼저 예약할 수 있어요.",
               now, seat=seat, key=f"offer:{e.id}:{now}")
    return offers


def wait_position(entry):
    """대기 순번(1부터): 나보다 먼저 등록한 대기자 수 + 1."""
    from django.db.models import Q
    return WaitEntry.objects.filter(status="waiting").filter(
        Q(created_at__lt=entry.created_at) | Q(created_at=entry.created_at, id__lte=entry.id)).count()


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
        return False, "체크인 후에 연장할 수 있어요."
    if res.extend_count >= s.max_extends:
        return False, f"연장은 최대 {s.max_extends}회까지 가능해요."
    if res.end_at - now > s.sec("extend_window_min"):
        return False, f"종료 {fmt_min(s.extend_window_min)} 전부터 연장할 수 있어요."
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
        "checkin_deadline": to_iso(res.start_at + s.sec("checkin_limit_min")) if res.status == "reserved" else None,
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
            # 장기 이석·사석화 비율 = (장기 이석 + 사석화) / (정상 이용 + 장기 이석 + 사석화)
            "issue_rate": round((x["away"] + x["hoarding"]) / base, 3) if base else 0.0,
        })
    return hours


# ---------------------------------------------------------------- 시연 상황 배치

# (좌석 라벨, 예약자 아이디, 예약 상태, 예약 시작(분 전; None=체크인 제한 초과), 현장 상태, mark, 사유, 현장 시작(분 전), 설명)
def setup_demo(now):
    """활성 예약·미해결 알림을 정리하고 seats.json의 "demo" 목록대로 다양한 상황을 만든다. 안내 문구 목록을 돌려준다.

    demo 항목: {"seat": 좌석 라벨, "user": 아이디, "reservation": "in_use"|"reserved", "start_ago_min": 예약 시작 몇 분 전
    (없으면 체크인 제한을 넘긴 예약 → 미입실), "state": 현장 상태, "mark", "reason", "state_ago_min", "note", "memo",
    "camera_unknown_ago_min": 카메라가 몇 분 전부터 판단 불가(UNKNOWN)였는지 — 판단 불가 시연용}
    """
    from .seed import load_layout
    msgs = []
    with transaction.atomic():
        s = get_settings()
        Reservation.objects.filter(status__in=ACTIVE).update(status="cancelled", ended_at=now)
        WaitEntry.objects.filter(status__in=("waiting", "offered")).update(status="cancelled", ended_at=now)
        Alert.objects.filter(resolved_at__isnull=True).update(resolved_at=now, resolution="reset")
        SeatState.objects.all().delete()  # 같은 상태라도 알림이 새로 생기게 캐시를 비운다
        seats = {st.label: st for st in Seat.objects.filter(active=True)}
        # 목록에 없는 좌석은 빈자리로
        demo = load_layout()["demo"]
        listed = {d["seat"] for d in demo}
        Seat.objects.filter(active=True).exclude(label__in=listed).update(
            state="empty", mark=None, reason=None, note=None, state_since=now, state_source="manual")
        for d in demo:
            label, sno, status, start_ago = d["seat"], d.get("user"), d.get("reservation", "in_use"), d.get("start_ago_min")
            seat = seats.get(label)
            if seat is None:
                continue
            seat.state, seat.mark, seat.reason = d.get("state", "empty"), d.get("mark"), d.get("reason")
            seat.note, seat.state_since = d.get("note"), now - int(d.get("state_ago_min", 0)) * 60
            seat.state_source = "manual"
            if d.get("camera_unknown_ago_min") is not None:  # 카메라가 이 좌석을 판단하지 못하는 상황
                seat.state_source, seat.cam_state = "camera", "UNKNOWN"
                seat.cam_unknown_since = now - int(round(float(d["camera_unknown_ago_min"]) * 60))
            seat.save()
            who = "예약 없음"
            if sno:
                user = User.objects.filter(student_no=sno).first()
                if user is None:
                    continue
                if start_ago is None:  # 체크인 제한을 넘긴 예약 → 다음 refresh에서 미입실 처리
                    start_ago = s.checkin_limit_min + 5  # 분
                start = now - int(round(start_ago * 60))
                res = Reservation.objects.create(user=user, seat=seat, status=status, start_at=start,
                                                 end_at=start + s.sec("default_use_min"),
                                                 checked_in_at=start if status == "in_use" else None)
                record_event(res, "reserve", start)
                if status == "in_use":
                    record_event(res, "checkin", start)
                who = f"{user.name} {'이용 중' if status == 'in_use' else '예약'}"
            msgs.append(f"{label}: {who} → {d.get('memo', '')}")
        refresh(now)
    return msgs


def default_setting_rows():
    return [Setting(key=k, value=str(v)) for k, v in DEFAULT_SETTINGS.items()]


