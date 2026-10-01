"""사용자 API. 일반 사용자에게는 좌석 3상태만 보이고, 처리 필요(!) 표시는 관리자 모드에서만 내려준다."""
import hmac

from django.db import IntegrityError, transaction
from django.views.decorators.http import require_GET, require_POST

from .. import clock
from ..auth import login_required
from ..http import ApiError, int_field, jres, json_body, str_field
from .. import analytics
from ..models import Alert, Notification, Reservation, Seat, WaitEntry
from ..seed import load_layout
from ..services import (clear_marks, extend_check, get_settings, record_event, refresh, reservation_json,
                        set_seat_state, user_active_reservation, wait_position)
from ..status import DETAILS, USER_MESSAGES, fmt_sec, user_view
from ..timeutil import to_iso

# 본인 좌석 상태 안내(사전 경고용). 내 예약 좌석에 한해서만 세부 상태를 알려 준다.
OWN_STATUS = {
    "waiting": ("info", "체크인 대기 중이에요. 좌석 QR로 체크인해 주세요."),
    "seated_unchecked": ("warn", "착석이 확인됐어요. {left} 안에 좌석 QR로 체크인해 주세요."),
    "no_checkin": ("warn", "착석(또는 짐)이 확인됐지만 체크인 전이에요. 좌석 QR로 체크인해 주세요."),
    "away_short": ("warn", "자리를 비운 상태예요(일시 이석). {left} 뒤 '장기 이석'으로 처리됩니다."),
    "item": ("warn", "짐만 두고 자리를 비운 상태예요. {left} 뒤 '사석화'로 처리됩니다."),
    "away": ("danger", "'장기 이석'으로 표시됐어요. {auto}"),
    "hoarding": ("danger", "'사석화'로 표시됐어요. {auto}"),
    "unauthorized": ("warn", "내 예약 좌석에 다른 이용이 확인되어 관리자가 확인 중이에요."),
    "no_show": ("danger", "체크인 시간이 지나 '미입실'로 표시됐어요. 도착했다면 바로 QR로 체크인해 주세요. {auto}"),
    "seat_unavailable": ("danger", "예약 좌석이 사용불가 상태예요. 관리자가 좌석을 옮겨 드리거나, 반납 후 다시 예약해 주세요."),
    "unknown": ("info", "카메라가 좌석을 확인하지 못하고 있어 관리자가 확인 중이에요. 계속 이용하셔도 돼요."),
}
# 카메라 판단 불가 중에는 기준 시간을 세지 않으므로 남은 시간({left}) 없는 문장을 쓴다.
OWN_STATUS_PAUSED = {
    "seated_unchecked": "착석이 확인됐어요. 좌석 QR로 체크인해 주세요.",
    "away_short": "자리를 비운 상태예요(일시 이석). 자리로 돌아오시거나 반납해 주세요.",
    "item": "짐만 두고 자리를 비운 상태예요. 자리로 돌아오시거나 반납해 주세요.",
}

CALL_DEDUP_SEC = 60  # 같은 사용자 60초 내 중복 호출은 기존 알림 반환


def token_ok(given, seat):
    return bool(given) and isinstance(given, str) and hmac.compare_digest(given, seat.qr_token)


def layout_json():
    layout = load_layout()
    booths = {s["no"] for s in layout["seats"] if s.get("booth")}
    return layout, booths


def _seat_view(item, my, uid=None):
    res, offer = item["res"], item.get("offer")
    if my and res and res.id == my.id:
        return "mine"
    if offer is not None and uid is not None:
        return "offered" if offer.user_id == uid else "held"  # 빈자리 알림 대기자에게 안내 중인 좌석
    return user_view(item["j"].seat_state)


def _fmt_left(sec):
    return fmt_sec(sec)


def own_status(results, my, now):
    """내 예약 좌석의 상태 안내(사전 경고 배너용)."""
    if not my:
        return None
    it = next((x for x in results if x["seat"].no == my.seat_id), None)
    if it is None or it["j"].detail not in OWN_STATUS:
        return None
    j = it["j"]
    level, msg = OWN_STATUS[j.detail]
    left = (j.deadline - now) if j.deadline else None
    if j.detail in OWN_STATUS_PAUSED and (left is None or j.next_detail == "unknown"):
        msg, left = OWN_STATUS_PAUSED[j.detail], None
    auto_sec = get_settings().sec("auto_return_min")
    if auto_sec > 0:
        auto_left = j.since + auto_sec - now
        auto = (f"{_fmt_left(auto_left)} 뒤 예약이 자동으로 {'취소' if my.status == 'reserved' else '반납'}돼요."
                if auto_left > 0 else "곧 자동으로 반납돼요.")
        if j.detail != "no_show":
            auto = "바로 돌아가거나 반납해 주세요. " + auto
    else:
        auto = "바로 돌아가거나 반납해 주세요. 관리자가 반납 처리할 수 있어요." if j.detail != "no_show" else "관리자가 예약을 취소할 수 있어요."
    return {"detail": j.detail, "label": DETAILS[j.detail][1], "level": level,
            "message": msg.format(left=_fmt_left(left) if left is not None else "", auto=auto),
            "deadline": to_iso(j.deadline)}


def waitlist_json(user_id, now):
    e = WaitEntry.objects.filter(user_id=user_id, status__in=("waiting", "offered")).select_related("offered_seat").first()
    if e is None:
        return None
    d = {"id": e.id, "status": e.status, "zone": e.zone, "created_at": to_iso(e.created_at)}
    if e.status == "waiting":
        d["position"] = wait_position(e)
    else:
        d["offer"] = {"seat_no": e.offered_seat_id, "seat_label": e.offered_seat.label, "expires_at": to_iso(e.expires_at),
                      "left_sec": max(0, e.expires_at - now)}
    return d


def _policy(s):
    return {"default_use_min": s.default_use_min, "checkin_limit_min": s.checkin_limit_min,
            "extend_window_min": s.extend_window_min, "extend_min": s.extend_min, "max_extends": s.max_extends,
            "hold_min": s.waitlist_hold_min}


def _me(request, now):
    u = request.user
    u.refresh_from_db(fields=["warnings", "suspended_until"])
    return {"name": u.name, "warnings": u.warnings, "suspended_until": to_iso(u.suspended(now)),
            "admin": request.admin}


def _find(results, seat_no):
    for it in results:
        if it["seat"].no == seat_no:
            return it
    raise ApiError(404, "NOT_FOUND", "좌석을 찾을 수 없습니다.")


def _own(request, res_id):
    r = Reservation.objects.select_related("seat").filter(id=res_id).first()
    if r is None or r.user_id != request.user.id:
        raise ApiError(404, "NOT_FOUND", "예약을 찾을 수 없습니다.")
    return r


# ---------------------------------------------------------------- 조회

@require_GET
@login_required
def seats(request):
    now = clock.now()
    results = refresh(now)
    s = get_settings()
    my = user_active_reservation(request.user.id)
    layout, booths = layout_json()
    out = []
    for it in results:
        seat, j = it["seat"], it["j"]
        row = {"no": seat.no, "label": seat.label, "x": seat.x, "y": seat.y, "zone": seat.zone,
               "booth": seat.no in booths, "view": _seat_view(it, my, request.user.id),
               # 좌석을 누르면 보이는 상태 이름·안내 (관리자 화면과 같은 이름). 색은 3가지로만 칠한다.
               "state_label": DETAILS[j.detail][1], "state_msg": USER_MESSAGES.get(j.detail, "")}
        if request.admin:  # 관리자 모드에서만 붉은 강조·'!'(확인 필요)
            row["attention"] = j.check
            row["detail_label"] = DETAILS[j.detail][1]
        out.append(row)
    return jres({
        "server_time": to_iso(now), "grid": layout["grid"], "fixtures": layout["fixtures"], "zones": layout["zones"],
        "seats": out, "my_reservation": reservation_json(my, now, s), "my_status": own_status(results, my, now),
        "waitlist": waitlist_json(request.user.id, now), "policy": _policy(s), "me": _me(request, now),
        "live": _live(request, results),
    })


def _live(request, results):
    live = analytics.live_usage(results)
    if not request.admin:  # 실사용 수치는 관리자 모드에서만
        live.pop("actual")
        live.pop("actual_rate")
    return live


@require_GET
@login_required
def seat_detail(request, no):
    now = clock.now()
    it = _find(refresh(now), no)
    s = get_settings()
    my = user_active_reservation(request.user.id)
    seat, res, j = it["seat"], it["res"], it["j"]
    token = request.GET.get("t")
    if res and my and res.id == my.id:
        mode = "mine_checkin" if res.status == "reserved" else "mine_in_use"
    elif res:
        mode = "reserved_by_other"
    elif seat.state == "unavailable":
        mode = "unavailable"
    else:
        mode = "reserve_now"
    return jres({
        "server_time": to_iso(now), "no": seat.no, "label": seat.label, "zone": seat.zone,
        "view": _seat_view(it, my, request.user.id), "page_mode": mode,
        "held": it.get("offer") is not None and it["offer"].user_id != request.user.id,
        "unavailable": seat.state == "unavailable",
        "unavailable_label": DETAILS[j.detail][1] if seat.state == "unavailable" and not res else None,
        "unavailable_note": seat.note if seat.state == "unavailable" else None,
        "occupied": res is None and seat.state in ("occupied", "item"),  # 누군가 앉아 있거나 짐이 있음
        "qr_ok": None if not token else token_ok(token, seat),
        "my_reservation": reservation_json(my, now, s), "my_status": own_status([it], my, now),
        "policy": _policy(s), "me": _me(request, now),
    })


# ---------------------------------------------------------------- 예약

@require_POST
@login_required
def create_reservation(request):
    body = json_body(request)
    seat_no = int_field(body, "seat_no")
    qr_token = body.get("qr_token") or None
    now = clock.now()
    with transaction.atomic():
        it = _find(refresh(now), seat_no)
        seat = it["seat"]
        request.user.refresh_from_db(fields=["suspended_until"])
        until = request.user.suspended(now)
        if until:
            raise ApiError(403, "SUSPENDED", f"이용 정지 중입니다. ({to_iso(until)[:16].replace('T', ' ')}까지)")
        if user_active_reservation(request.user.id):
            raise ApiError(409, "ALREADY_HAS_RESERVATION", "이미 이용 중인 예약이 있습니다. 반납 후 다시 예약해 주세요.")
        if it["res"]:
            raise ApiError(409, "SEAT_TAKEN", "이미 예약된 좌석입니다.")
        if seat.state == "unavailable":
            raise ApiError(409, "SEAT_UNAVAILABLE", "사용할 수 없는 좌석입니다.")
        offer = it.get("offer")
        if offer is not None and offer.user_id != request.user.id:
            raise ApiError(409, "SEAT_HELD", "빈자리 알림 대기자에게 먼저 안내된 좌석입니다. 잠시 후 다시 확인해 주세요.")
        ok = token_ok(qr_token, seat)
        if seat.state in ("occupied", "item") and not ok:
            raise ApiError(409, "SEAT_OCCUPIED", "현재 다른 이용자가 사용 중인(또는 짐이 있는) 좌석입니다.")
        if qr_token and not ok:
            raise ApiError(403, "BAD_QR_TOKEN", "좌석 QR을 다시 스캔해 주세요.")
        s = get_settings()
        try:
            with transaction.atomic():
                res = Reservation.objects.create(
                    user=request.user, seat=seat, status="in_use" if ok else "reserved", start_at=now,
                    end_at=now + s.sec("default_use_min"), checked_in_at=now if ok else None,
                    source="seat_page" if ok else "map")
        except IntegrityError:
            raise ApiError(409, "SEAT_TAKEN", "방금 다른 이용자가 예약했습니다.")
        record_event(res, "reserve", now, memo="좌석 QR" if ok else "좌석 지도")
        if ok:
            record_event(res, "checkin", now, memo="좌석 QR로 바로 예약")
            # 좌석 QR 체크인은 본인이 좌석에 있다는 뜻이므로 현장 상태를 '사람 있음'으로 둔다.
            set_seat_state(seat, "occupied", "checkin", now)
        # 빈자리 알림 대기 중이었다면 대기 완료
        WaitEntry.objects.filter(user=request.user, status__in=("waiting", "offered")).update(status="fulfilled", ended_at=now)
        refresh(now)
        return jres(reservation_json(res, now, s), 201)


@require_POST
@login_required
def checkin(request, res_id):
    body = json_body(request)
    now = clock.now()
    with transaction.atomic():
        refresh(now)
        r = _own(request, res_id)
        if not token_ok(body.get("qr_token"), r.seat):
            raise ApiError(403, "BAD_QR_TOKEN", "좌석 QR을 다시 스캔해 주세요.")
        if r.status != "reserved":
            raise ApiError(409, "INVALID_STATE", "체크인할 수 있는 예약이 아닙니다.")
        if r.seat.state == "unavailable":
            raise ApiError(409, "SEAT_UNAVAILABLE", "사용할 수 없는 좌석입니다. 관리자에게 좌석 이동을 요청해 주세요.")
        r.status, r.checked_in_at = "in_use", now
        r.save(update_fields=["status", "checked_in_at"])
        record_event(r, "checkin", now, memo="좌석 QR")
        set_seat_state(r.seat, "occupied", "checkin", now)
        refresh(now)
        return jres(reservation_json(r, now, get_settings()))


@require_POST
@login_required
def extend(request, res_id):
    now = clock.now()
    with transaction.atomic():
        refresh(now)
        r = _own(request, res_id)
        s = get_settings()
        ok, reason = extend_check(r, now, s)
        if not ok:
            raise ApiError(409, "EXTEND_NOT_ALLOWED", reason)
        old_end = r.end_at
        r.end_at += s.sec("extend_min")
        r.extend_count += 1
        r.save(update_fields=["end_at", "extend_count"])
        record_event(r, "extend", now, memo=f"종료 {to_iso(old_end)[11:16]} → {to_iso(r.end_at)[11:16]}")
        refresh(now)
        return jres(reservation_json(r, now, s))


@require_POST
@login_required
def return_reservation(request, res_id):
    now = clock.now()
    with transaction.atomic():
        refresh(now)
        r = _own(request, res_id)
        if r.status not in ("reserved", "in_use"):
            raise ApiError(409, "INVALID_STATE", "이미 종료된 예약입니다.")
        new_status = "cancelled" if r.status == "reserved" else "returned"
        r.status, r.ended_at = new_status, now
        r.save(update_fields=["status", "ended_at"])
        record_event(r, "cancel" if new_status == "cancelled" else "return", now,
                     memo=None if new_status == "cancelled" else f"남은 시간 {_fmt_left(max(0, r.end_at - now))}")
        # 반납은 자리를 정리하고 떠난다는 뜻. 카메라가 붙어 있으면 다음 감지로 바로잡힌다.
        if new_status == "returned" and r.seat.state == "occupied":
            set_seat_state(r.seat, "empty", "return", now)
        clear_marks([r.seat_id])
        refresh(now)
    return jres({"ok": True, "status": new_status})


# ---------------------------------------------------------------- 관리자 호출

@require_POST
@login_required
def create_call(request):
    body = json_body(request)
    seat_no = int_field(body, "seat_no")
    memo = str_field(body, "memo")
    now = clock.now()
    with transaction.atomic():
        seat = Seat.objects.filter(no=seat_no, active=True).first()
        if seat is None:
            raise ApiError(404, "NOT_FOUND", "좌석을 찾을 수 없습니다.")
        dup = Alert.objects.filter(type="call", created_by=request.user,
                                   created_at__gt=now - CALL_DEDUP_SEC).order_by("-id").first()
        if dup:
            return jres({"ok": True, "duplicate": True, "alert": _call_json(dup)})
        res = Reservation.objects.filter(seat=seat, status__in=("reserved", "in_use")).first()
        a = Alert.objects.create(seat=seat, type="call", reservation=res, memo=memo, created_at=now,
                                 created_by=request.user)
    return jres({"ok": True, "duplicate": False, "alert": _call_json(a)}, 201)


def _call_json(a):
    return {"id": a.id, "seat_no": a.seat_id, "memo": a.memo, "created_at": to_iso(a.created_at)}


# ---------------------------------------------------------------- 빈자리 알림 대기

@require_POST
@login_required
def waitlist_join(request):
    body = json_body(request)
    zone = str_field(body, "zone", 50) or ""
    now = clock.now()
    with transaction.atomic():
        layout = load_layout()
        if zone and zone not in {st.get("zone") for st in layout["seats"]}:
            raise ApiError(400, "BAD_REQUEST", "알 수 없는 구역입니다.")
        request.user.refresh_from_db(fields=["suspended_until"])
        if request.user.suspended(now):
            raise ApiError(403, "SUSPENDED", "이용 정지 중에는 빈자리 알림을 신청할 수 없습니다.")
        if user_active_reservation(request.user.id):
            raise ApiError(409, "ALREADY_HAS_RESERVATION", "이미 이용 중인 예약이 있습니다.")
        if WaitEntry.objects.filter(user=request.user, status__in=("waiting", "offered")).exists():
            raise ApiError(409, "ALREADY_WAITING", "이미 빈자리 알림을 기다리고 있어요.")
        WaitEntry.objects.create(user=request.user, zone=zone, created_at=now)
        refresh(now)  # 조건에 맞는 빈자리가 있으면 바로 안내
    return jres({"ok": True, "waitlist": waitlist_json(request.user.id, now)}, 201)


def _end_wait(request, status):
    now = clock.now()
    with transaction.atomic():
        n = WaitEntry.objects.filter(user=request.user, status__in=("waiting", "offered")).update(status=status, ended_at=now)
        if not n:
            raise ApiError(409, "INVALID_STATE", "기다리는 빈자리 알림이 없습니다.")
        refresh(now)  # 양보한 좌석은 다음 대기자에게
    return jres({"ok": True})


@require_POST
@login_required
def waitlist_cancel(request):
    return _end_wait(request, "cancelled")


@require_POST
@login_required
def waitlist_decline(request):
    return _end_wait(request, "declined")


# ---------------------------------------------------------------- 내 이용 기록·알림

@require_GET
@login_required
def my_history(request):
    now = clock.now()
    refresh(now)
    return jres(analytics.user_history(request.user, request.GET.get("period", "day"), now))


@require_GET
@login_required
def my_reservations(request):
    now = clock.now()
    return jres({"reservations": analytics.user_reservations(request.user, now)})


@require_GET
@login_required
def my_notifications(request):
    qs = Notification.objects.filter(user=request.user).select_related("seat").order_by("-created_at", "-id")
    return jres({
        "unread": qs.filter(read_at__isnull=True).count(),
        "items": [{"id": n.id, "kind": n.kind, "level": n.level, "title": n.title, "body": n.body,
                   "seat_label": n.seat.label if n.seat else None, "seat_no": n.seat_id,
                   "created_at": to_iso(n.created_at), "read": n.read_at is not None} for n in qs[:40]],
    })


@require_POST
@login_required
def my_notifications_read(request):
    body = json_body(request)
    qs = Notification.objects.filter(user=request.user, read_at__isnull=True)
    ids = body.get("ids")
    if isinstance(ids, list):
        qs = qs.filter(id__in=[i for i in ids if isinstance(i, int)])
    return jres({"ok": True, "read": qs.update(read_at=clock.now())})


# ---------------------------------------------------------------- 혼잡도

@require_GET
@login_required
def congestion(request):
    now = clock.now()
    results = refresh(now)
    s = get_settings()
    data = analytics.congestion(now, s, weeks=4)
    if not request.admin:  # 실사용률·유휴 점유·처리 필요 비율은 관리자 모드에서만
        for k in ("actual", "idle", "issue"):
            data.pop(k)
        for row in data["today"]:
            row.pop("actual")
    data["live"] = _live(request, results)
    data["admin"] = request.admin
    data["server_time"] = to_iso(now)
    return jres(data)
