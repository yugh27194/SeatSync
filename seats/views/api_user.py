"""사용자 API. 일반 사용자에게는 좌석 3상태만 보이고, 처리 필요(!) 표시는 관리자 모드에서만 내려준다."""
import hmac

from django.db import IntegrityError, transaction
from django.views.decorators.http import require_GET, require_POST

from .. import clock
from ..auth import login_required
from ..http import ApiError, int_field, jres, json_body, str_field
from ..models import Alert, Reservation, Seat
from ..seed import load_layout
from ..services import (clear_marks, extend_check, get_settings, refresh, reservation_json, set_seat_state,
                        user_active_reservation)
from ..status import DETAILS, user_view
from ..timeutil import to_iso

CALL_DEDUP_SEC = 60  # 같은 사용자 60초 내 중복 호출은 기존 알림 반환


def token_ok(given, seat):
    return bool(given) and isinstance(given, str) and hmac.compare_digest(given, seat.qr_token)


def layout_json():
    layout = load_layout()
    booths = {s["no"] for s in layout["seats"] if s.get("booth")}
    return layout, booths


def _seat_view(item, my):
    res = item["res"]
    if my and res and res.id == my.id:
        return "mine"
    return user_view(item["j"].seat_state)


def _policy(s):
    return {"default_use_min": s.default_use_min, "checkin_limit_min": s.checkin_limit_min,
            "extend_window_min": s.extend_window_min, "extend_min": s.extend_min, "max_extends": s.max_extends}


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
               "booth": seat.no in booths, "view": _seat_view(it, my)}
        if request.admin:  # 관리자 모드에서만 '처리 필요' 표시
            row["attention"] = j.needs_action
            row["detail_label"] = DETAILS[j.detail][1]
        out.append(row)
    return jres({
        "server_time": to_iso(now), "grid": layout["grid"], "fixtures": layout["fixtures"], "zones": layout["zones"],
        "seats": out, "my_reservation": reservation_json(my, now, s), "policy": _policy(s), "me": _me(request, now),
    })


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
        "view": _seat_view(it, my), "page_mode": mode,
        "unavailable": seat.state == "unavailable",
        "unavailable_label": DETAILS[j.detail][1] if seat.state == "unavailable" and not res else None,
        "unavailable_note": seat.note if seat.state == "unavailable" else None,
        "occupied": res is None and seat.state in ("occupied", "item"),  # 누군가 앉아 있거나 짐이 있음
        "qr_ok": None if not token else token_ok(token, seat),
        "my_reservation": reservation_json(my, now, s), "policy": _policy(s), "me": _me(request, now),
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
                    end_at=now + s.default_use_min * 60, checked_in_at=now if ok else None,
                    source="seat_page" if ok else "map")
        except IntegrityError:
            raise ApiError(409, "SEAT_TAKEN", "방금 다른 이용자가 예약했습니다.")
        if ok:
            # 좌석 QR 체크인은 본인이 좌석에 있다는 뜻이므로 현장 상태를 '사람 있음'으로 둔다.
            set_seat_state(seat, "occupied", "checkin", now)
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
        r.end_at += s.extend_min * 60
        r.extend_count += 1
        r.save(update_fields=["end_at", "extend_count"])
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
