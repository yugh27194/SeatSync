"""관리자 API: 좌석 현황·세부 상태 부여, 처리 필요 알림, 상황별 조치, 이용자 경고·정지, 처리 이력, 설정, 통계."""
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .. import clock
from ..auth import admin_required
from ..http import ApiError, int_field, jres, json_body, str_field
from .. import analytics
from ..models import AdminLog, Alert, JudgmentFeedback, Reservation, Seat, Setting, User, WaitEntry
from ..services import (clear_marks, compute_hourly_stats, get_settings, log_admin, notify, record_event, refresh,
                        set_seat_state, setup_demo)
from ..status import (ACTUAL_STATES, ALERT_TYPE_LABELS, ASSIGN_GROUPS, ASSIGNABLE, DEFAULT_SETTINGS, DETAILS,
                      SEAT_STATES, SETTINGS_META)
from ..timeutil import to_iso, tz
from .api_user import layout_json

MAX_SUSPEND_DAYS = 90
ACTION_LABELS = {
    "resolve": "처리 완료", "seat_state": "좌석 상태 지정", "assign": "대리 예약", "checkin": "대리 체크인",
    "move": "좌석 이동", "extend": "관리자 연장", "force_return": "강제 반납", "warn": "경고 부여",
    "unwarn": "경고 취소", "suspend": "이용 정지", "unsuspend": "정지 해제", "demo": "시연 상황 배치",
    "settings": "설정 변경", "admin_on": "관리자 모드 켬", "admin_off": "관리자 모드 끔", "admin_locked": "관리자 코드 잠금",
    "notice": "사전 경고 발송", "feedback": "판정 피드백", "demo_history": "샘플 이력 생성",
}
RES_STATUS = {"reserved": "예약(입실 전)", "in_use": "이용 중"}


def _user_summary(u, now, s):
    if u is None:
        return None
    until = u.suspended(now)
    return {"id": u.id, "name": u.name, "student_no": u.student_no, "warnings": u.warnings,
            "suspended_until": to_iso(until), "suspend_suggested": u.warnings >= s.warning_limit and not until}


def _res_summary(r, now, s):
    if not r:
        return None
    return {"id": r.id, "status": r.status, "status_label": RES_STATUS.get(r.status, r.status),
            "start_at": to_iso(r.start_at), "end_at": to_iso(r.end_at), "checked_in_at": to_iso(r.checked_in_at),
            "remaining_sec": max(0, r.end_at - now), "source": r.source, "user": _user_summary(r.user, now, s)}


def _get_reservation(res_id, active=True):
    r = Reservation.objects.select_related("seat", "user").filter(id=res_id).first()
    if r is None:
        raise ApiError(404, "NOT_FOUND", "예약을 찾을 수 없습니다.")
    if active and r.status not in ("reserved", "in_use"):
        raise ApiError(409, "INVALID_STATE", "이미 종료된 예약입니다.")
    return r


def _get_user(user_id):
    u = User.objects.filter(id=user_id).first()
    if u is None:
        raise ApiError(404, "NOT_FOUND", "이용자를 찾을 수 없습니다.")
    return u


def _get_seat(no):
    seat = Seat.objects.filter(no=no, active=True).first()
    if seat is None:
        raise ApiError(404, "NOT_FOUND", "좌석을 찾을 수 없습니다.")
    return seat


def seat_id_or_none(no):
    return {"seat": Seat.objects.filter(no=no).first()} if no else {}


def _active_on(seat_no):
    return Reservation.objects.filter(seat_id=seat_no, status__in=("reserved", "in_use")).first()


def _ok(**extra):
    return jres({"ok": True, **extra})


# ---------------------------------------------------------------- 현황

@require_GET
@admin_required
def seats(request):
    now = clock.now()
    results = refresh(now)
    s = get_settings()
    layout, booths = layout_json()
    open_alerts = {}
    for a in Alert.objects.filter(resolved_at__isnull=True).exclude(type="call"):
        open_alerts.setdefault(a.seat_id, {})[a.type] = a.id
    summary = {k: 0 for k in SEAT_STATES}
    summary["issues"] = 0
    out = []
    for it in results:
        seat, j, res = it["seat"], it["j"], it["res"]
        summary[j.seat_state] += 1
        summary["issues"] += j.needs_action
        out.append({
            "no": seat.no, "label": seat.label, "x": seat.x, "y": seat.y, "zone": seat.zone, "booth": seat.no in booths,
            "seat_state": j.seat_state, "seat_state_label": SEAT_STATES[j.seat_state],
            "detail": j.detail, "detail_label": DETAILS[j.detail][1], "detail_desc": DETAILS[j.detail][3],
            "needs_action": j.needs_action,
            "since": to_iso(j.since), "elapsed_sec": max(0, now - j.since),
            "deadline": to_iso(j.deadline), "deadline_sec": (j.deadline - now) if j.deadline else None,
            "actual": seat.state, "actual_label": ACTUAL_STATES[seat.state], "mark": seat.mark, "reason": seat.reason,
            "actual_since": to_iso(seat.state_since), "actual_elapsed_sec": max(0, now - seat.state_since),
            "actual_source": seat.state_source, "note": seat.note,
            "reservation": _res_summary(res, now, s),
            "alert_id": open_alerts.get(seat.no, {}).get(j.detail),
            "offer": {"user_name": it["offer"].user.name, "expires_at": to_iso(it["offer"].expires_at),
                      "left_sec": max(0, it["offer"].expires_at - now)} if it.get("offer") else None,
        })
    return jres({
        "server_time": to_iso(now), "grid": layout["grid"], "fixtures": layout["fixtures"], "zones": layout["zones"],
        "summary": summary, "seats": out, "settings": s.as_dict(), "live": analytics.live_usage(results),
        "waiting": WaitEntry.objects.filter(status="waiting").count(),
        "assign": {"groups": [{"state": g, "label": SEAT_STATES[g], "items": [
            {"code": c, "label": DETAILS[c][1], "needs": ASSIGNABLE[c][3], "issue": DETAILS[c][2]} for c in codes]}
            for g, codes in ASSIGN_GROUPS]},
    })


@require_GET
@admin_required
def alerts(request):
    now = clock.now()
    refresh(now)
    s = get_settings()
    qs = Alert.objects.select_related("seat", "reservation__user", "created_by")
    if request.GET.get("open") in ("1", "true"):
        qs = qs.filter(resolved_at__isnull=True).order_by("created_at", "id")
    else:
        qs = qs.order_by("-created_at", "-id")[:200]
    active = {r.seat_id: r for r in Reservation.objects.filter(status__in=("reserved", "in_use"))}
    out = []
    for a in qs:
        r, ar = a.reservation, active.get(a.seat_id)
        out.append({
            "id": a.id, "seat_no": a.seat_id, "seat_label": a.seat.label, "seat_actual": a.seat.state,
            "type": a.type, "type_label": ALERT_TYPE_LABELS[a.type],
            "desc": DETAILS[a.type][3] if a.type in DETAILS else "",
            "created_at": to_iso(a.created_at), "elapsed_sec": max(0, now - a.created_at), "memo": a.memo,
            "caller": {"name": a.created_by.name, "student_no": a.created_by.student_no} if a.created_by else None,
            "reservation": {"id": r.id, "status": r.status, "start_at": to_iso(r.start_at), "end_at": to_iso(r.end_at),
                            "checked_in_at": to_iso(r.checked_in_at), "user": _user_summary(r.user, now, s)} if r else None,
            "active_reservation_id": ar.id if ar else None, "active_reservation_status": ar.status if ar else None,
            "resolved_at": to_iso(a.resolved_at), "resolution": a.resolution,
        })
    return jres({"server_time": to_iso(now), "alerts": out})


@require_POST
@admin_required
def resolve_alert(request, alert_id):
    body = json_body(request)
    now = clock.now()
    with transaction.atomic():
        a = Alert.objects.filter(id=alert_id).first()
        if a is None:
            raise ApiError(404, "NOT_FOUND", "알림을 찾을 수 없습니다.")
        if a.resolved_at is not None:
            raise ApiError(409, "INVALID_STATE", "이미 처리된 알림입니다.")
        a.resolved_at, a.resolved_by_id, a.resolution = now, request.user.id, "handled"
        a.save(update_fields=["resolved_at", "resolved_by", "resolution"])
        log_admin(request.user.id, "resolve", now, seat_no=a.seat_id, reservation_id=a.reservation_id, alert_id=a.id,
                  memo=str_field(body, "memo") or ALERT_TYPE_LABELS[a.type])
    return _ok()


# ---------------------------------------------------------------- 좌석 세부 상태 부여 (임시 배분)

@require_POST
@admin_required
def change_seat_state(request, no):
    body = json_body(request)
    code = body.get("detail")
    if code not in ASSIGNABLE:
        raise ApiError(400, "BAD_REQUEST", f"detail은 {', '.join(ASSIGNABLE)} 중 하나여야 합니다.")
    state, mark, reason, needs = ASSIGNABLE[code]
    note = str_field(body, "note")
    now = clock.now()
    with transaction.atomic():
        seat = _get_seat(no)
        if needs == "in_use":
            r = _active_on(no)
            if not r or r.status != "in_use":
                raise ApiError(409, "INVALID_STATE", f"'{DETAILS[code][1]}'은(는) 이용 중인 예약이 있는 좌석에만 지정할 수 있습니다.")
        set_seat_state(seat, state, "manual", now, mark=mark, reason=reason, note=note if state == "unavailable" else None)
        log_admin(request.user.id, "seat_state", now, seat_no=no,
                  memo=f"{SEAT_STATES[DETAILS[code][0]]} · {DETAILS[code][1]}" + (f" ({note})" if note else ""))
        refresh(now)
    return _ok()


# ---------------------------------------------------------------- 예약 조치

@require_POST
@admin_required
def assign(request):
    """대리 예약 / 현장 배정. checkin=true면 바로 이용 중(착석한 이용자에게 좌석 배정)."""
    body = json_body(request)
    user_id, seat_no = int_field(body, "user_id"), int_field(body, "seat_no")
    checkin = bool(body.get("checkin"))
    now = clock.now()
    with transaction.atomic():
        refresh(now)
        s = get_settings()
        u, seat = _get_user(user_id), _get_seat(seat_no)
        if u.suspended(now):
            raise ApiError(409, "SUSPENDED", f"{u.name} 님은 이용 정지 중입니다. 정지를 해제한 뒤 배정하세요.")
        if Reservation.objects.filter(user=u, status__in=("reserved", "in_use")).exists():
            raise ApiError(409, "ALREADY_HAS_RESERVATION", f"{u.name} 님은 이미 다른 예약이 있습니다.")
        if _active_on(seat_no):
            raise ApiError(409, "SEAT_TAKEN", "이미 예약된 좌석입니다.")
        if seat.state == "unavailable":
            raise ApiError(409, "SEAT_UNAVAILABLE", "사용불가 좌석입니다. 좌석 상태를 먼저 바꾸세요.")
        try:
            with transaction.atomic():
                r = Reservation.objects.create(user=u, seat=seat, status="in_use" if checkin else "reserved",
                                               start_at=now, end_at=now + s.default_use_min * 60,
                                               checked_in_at=now if checkin else None, source="admin")
        except IntegrityError:
            raise ApiError(409, "SEAT_TAKEN", "방금 다른 예약이 생겼습니다.")
        record_event(r, "admin_assign", now)
        if checkin:
            record_event(r, "admin_checkin", now)
            set_seat_state(seat, "occupied", "manual", now)
        WaitEntry.objects.filter(user=u, status__in=("waiting", "offered")).update(status="fulfilled", ended_at=now)
        notify(u.id, "info", "ok", f"{seat.label} 좌석이 배정됐어요",
               "관리자가 좌석을 배정했습니다." + ("" if checkin else " 좌석 QR로 체크인해 주세요."), now, seat=seat, reservation=r)
        memo = str_field(body, "memo")
        log_admin(request.user.id, "assign", now, seat_no=seat_no, reservation_id=r.id, target_user_id=u.id,
                  memo=("바로 이용 시작" if checkin else "입실 전 예약") + (f" · {memo}" if memo else ""))
        refresh(now)
    return jres({"ok": True, "reservation_id": r.id}, 201)


@require_POST
@admin_required
def admin_checkin(request, res_id):
    now = clock.now()
    with transaction.atomic():
        refresh(now)
        r = _get_reservation(res_id)
        if r.status != "reserved":
            raise ApiError(409, "INVALID_STATE", "체크인 전 예약만 대리 체크인할 수 있습니다.")
        if r.seat.state == "unavailable":
            raise ApiError(409, "SEAT_UNAVAILABLE", "사용불가 좌석입니다. 좌석을 먼저 이동하세요.")
        r.status, r.checked_in_at = "in_use", now
        r.save(update_fields=["status", "checked_in_at"])
        record_event(r, "admin_checkin", now)
        set_seat_state(r.seat, "occupied", "manual", now)
        log_admin(request.user.id, "checkin", now, seat_no=r.seat_id, reservation_id=r.id, target_user_id=r.user_id)
        refresh(now)
    return _ok()


@require_POST
@admin_required
def move(request, res_id):
    body = json_body(request)
    to_no = int_field(body, "seat_no")
    now = clock.now()
    with transaction.atomic():
        refresh(now)
        r = _get_reservation(res_id)
        if to_no == r.seat_id:
            raise ApiError(400, "BAD_REQUEST", "현재 좌석과 같은 좌석입니다.")
        target = _get_seat(to_no)
        if _active_on(to_no):
            raise ApiError(409, "SEAT_TAKEN", "옮길 좌석에 이미 예약이 있습니다.")
        if target.state != "empty":
            raise ApiError(409, "SEAT_OCCUPIED" if target.state in ("occupied", "item") else "SEAT_UNAVAILABLE",
                           "빈자리로만 옮길 수 있습니다.")
        old = r.seat
        r.seat = target
        r.save(update_fields=["seat"])
        if r.status == "in_use":
            # 이용 중인 이용자가 자리를 옮기므로 새 좌석은 사람 있음, 기존 좌석은(사용불가가 아니면) 비움
            set_seat_state(target, "occupied", "manual", now)
            if old.state == "occupied":
                set_seat_state(old, "empty", "manual", now)
        memo = str_field(body, "memo")
        record_event(r, "move", now, memo=f"{old.label} → {target.label}")
        notify(r.user_id, "info", "info", f"좌석이 {target.label}(으)로 옮겨졌어요",
               f"관리자가 {old.label} → {target.label}(으)로 좌석을 옮겼습니다." + (f" ({memo})" if memo else ""),
               now, seat=target, reservation=r)
        log_admin(request.user.id, "move", now, seat_no=to_no, reservation_id=r.id, target_user_id=r.user_id,
                  memo=f"{old.label} → {target.label}" + (f" · {memo}" if memo else ""))
        refresh(now)
    return _ok()


@require_POST
@admin_required
def admin_extend(request, res_id):
    now = clock.now()
    with transaction.atomic():
        refresh(now)
        r = _get_reservation(res_id)
        s = get_settings()
        # 관리자 연장은 연장 가능 시점·횟수 제한을 적용하지 않고, 이용자 연장 횟수에도 포함하지 않는다.
        old_end = r.end_at
        r.end_at += s.extend_min * 60
        r.save(update_fields=["end_at"])
        record_event(r, "admin_extend", now, memo=f"종료 {to_iso(old_end)[11:16]} → {to_iso(r.end_at)[11:16]}")
        log_admin(request.user.id, "extend", now, seat_no=r.seat_id, reservation_id=r.id, target_user_id=r.user_id,
                  memo=f"+{s.extend_min}분")
        refresh(now)
    return _ok()


@require_POST
@admin_required
def force_return(request, res_id):
    body = json_body(request)
    now = clock.now()
    with transaction.atomic():
        r = _get_reservation(res_id)
        r.status, r.ended_at = "force_returned", now
        r.save(update_fields=["status", "ended_at"])
        Alert.objects.filter(seat_id=r.seat_id, resolved_at__isnull=True).update(
            resolved_at=now, resolved_by_id=request.user.id, resolution="force_returned")
        # 관리자 지정 의도는 예약과 함께 정리한다(남은 짐·사람은 예약 없는 좌석으로 다시 판정됨)
        clear_marks([r.seat_id])
        memo = str_field(body, "memo")
        record_event(r, "force_return", now, memo=memo)
        notify(r.user_id, "issue", "danger", f"{r.seat.label} 예약이 관리자에 의해 반납됐어요",
               memo or "좌석 이용 규정 위반으로 관리자가 반납 처리했습니다.", now, seat=r.seat, reservation=r)
        log_admin(request.user.id, "force_return", now, seat_no=r.seat_id, reservation_id=r.id,
                  target_user_id=r.user_id, memo=memo)
        refresh(now)
    return _ok()


# ---------------------------------------------------------------- 이용자 관리 (경고·정지)

@require_GET
@admin_required
def users(request):
    now = clock.now()
    s = get_settings()
    active = {r.user_id: r for r in Reservation.objects.filter(status__in=("reserved", "in_use")).select_related("seat")}
    out = []
    for u in User.objects.filter(is_active=True):
        d = _user_summary(u, now, s)
        r = active.get(u.id)
        d["reservation"] = {"id": r.id, "status": r.status, "status_label": RES_STATUS[r.status],
                            "seat_label": r.seat.label} if r else None
        out.append(d)
    return jres({"users": out, "warning_limit": s.warning_limit, "suspend_days": s.suspend_days})


@require_POST
@admin_required
def warn(request, user_id):
    body = json_body(request)
    alert_id = int_field(body, "alert_id", required=False)
    reason = str_field(body, "reason")
    now = clock.now()
    with transaction.atomic():
        u = _get_user(user_id)
        seat_no = None
        if alert_id is not None:
            a = Alert.objects.filter(id=alert_id).first()
            if a is None:
                raise ApiError(404, "NOT_FOUND", "알림을 찾을 수 없습니다.")
            seat_no = a.seat_id
            reason = reason or ALERT_TYPE_LABELS[a.type]
            if body.get("resolve") and a.resolved_at is None:
                a.resolved_at, a.resolved_by_id, a.resolution = now, request.user.id, "warned"
                a.save(update_fields=["resolved_at", "resolved_by", "resolution"])
        u.warnings += 1
        u.save(update_fields=["warnings"])
        s = get_settings()
        notify(u.id, "warning", "danger", f"경고가 부여됐어요 (누적 {u.warnings}회)",
               (f"사유: {reason}. " if reason else "") + f"경고가 {s.warning_limit}회 이상 쌓이면 이용이 정지될 수 있어요.",
               now, **seat_id_or_none(seat_no))
        log_admin(request.user.id, "warn", now, seat_no=seat_no, target_user_id=u.id, alert_id=alert_id, memo=reason)
    return _ok(warnings=u.warnings, suspend_suggested=u.warnings >= s.warning_limit and not u.suspended(now))


@require_POST
@admin_required
def unwarn(request, user_id):
    body = json_body(request)
    now = clock.now()
    with transaction.atomic():
        u = _get_user(user_id)
        if u.warnings <= 0:
            raise ApiError(409, "INVALID_STATE", "취소할 경고가 없습니다.")
        u.warnings -= 1
        u.save(update_fields=["warnings"])
        notify(u.id, "info", "ok", f"경고 1회가 취소됐어요 (누적 {u.warnings}회)", "", now)
        log_admin(request.user.id, "unwarn", now, target_user_id=u.id, memo=str_field(body, "reason"))
    return _ok(warnings=u.warnings)


@require_POST
@admin_required
def suspend(request, user_id):
    body = json_body(request)
    now = clock.now()
    s = get_settings()
    days = int_field(body, "days", required=False) or s.suspend_days
    if not 1 <= days <= MAX_SUSPEND_DAYS:
        raise ApiError(400, "BAD_REQUEST", f"정지 기간은 1~{MAX_SUSPEND_DAYS}일이어야 합니다.")
    with transaction.atomic():
        u = _get_user(user_id)
        u.suspended_until = now + days * 86400
        u.save(update_fields=["suspended_until"])
        reason = str_field(body, "reason")
        notify(u.id, "suspend", "danger", f"이용이 {days}일 정지됐어요",
               f"{to_iso(u.suspended_until)[5:16].replace('T', ' ')}까지 새 예약을 할 수 없어요." + (f" 사유: {reason}" if reason else ""), now)
        log_admin(request.user.id, "suspend", now, target_user_id=u.id, memo=f"{days}일" + (f" · {reason}" if reason else ""))
    return _ok(suspended_until=to_iso(u.suspended_until))


@require_POST
@admin_required
def unsuspend(request, user_id):
    now = clock.now()
    with transaction.atomic():
        u = _get_user(user_id)
        if not u.suspended(now):
            raise ApiError(409, "INVALID_STATE", "정지 중인 이용자가 아닙니다.")
        u.suspended_until = None
        u.save(update_fields=["suspended_until"])
        notify(u.id, "info", "ok", "이용 정지가 해제됐어요", "다시 좌석을 예약할 수 있어요.", now)
        log_admin(request.user.id, "unsuspend", now, target_user_id=u.id)
    return _ok()


# ---------------------------------------------------------------- 처리 이력

@require_GET
@admin_required
def admin_log(request):
    try:
        limit = min(max(int(request.GET.get("limit", 50)), 1), 200)
    except ValueError:
        limit = 50
    rows = AdminLog.objects.select_related("admin", "target_user", "seat").order_by("-at", "-id")[:limit]
    return jres({"log": [{
        "id": r.id, "at": to_iso(r.at), "action": r.action, "action_label": ACTION_LABELS.get(r.action, r.action),
        "admin_name": r.admin.name if r.admin else None, "seat_label": r.seat.label if r.seat else None,
        "user_name": r.target_user.name if r.target_user else None,
        "user_student_no": r.target_user.student_no if r.target_user else None, "memo": r.memo,
    } for r in rows]})


# ---------------------------------------------------------------- 시연 상황 배치

@require_POST
@admin_required
def demo(request):
    now = clock.now()
    msgs = setup_demo(now)
    log_admin(request.user.id, "demo", now, memo=f"{len(msgs)}개 좌석 배치")
    return _ok(messages=msgs)


# ---------------------------------------------------------------- 설정

def _settings_json(s):
    return {"settings": s.as_dict(), "defaults": DEFAULT_SETTINGS,
            "meta": {k: {"label": m[0], "unit": m[1], "desc": m[2], "min": m[3], "max": m[4]} for k, m in SETTINGS_META.items()}}


@require_http_methods(["GET", "PUT"])
@admin_required
def settings_api(request):
    if request.method == "GET":
        return jres(_settings_json(get_settings()))
    body = json_body(request)
    if not body:
        raise ApiError(400, "BAD_REQUEST", "변경할 설정이 없습니다.")
    clean = {}
    for k, v in body.items():
        if k not in SETTINGS_META:
            raise ApiError(400, "BAD_REQUEST", f"알 수 없는 설정 키: {k}")
        label, unit, _desc, lo, hi = SETTINGS_META[k]
        try:
            if isinstance(v, bool) or (isinstance(v, float) and not v.is_integer()):
                raise ValueError
            iv = int(v)
        except (TypeError, ValueError):
            raise ApiError(400, "BAD_REQUEST", f"'{label}' 값은 정수여야 합니다.")
        if not lo <= iv <= hi:
            raise ApiError(400, "BAD_REQUEST", f"'{label}' 값은 {lo}~{hi} {unit} 범위여야 합니다.")
        clean[k] = iv
    now = clock.now()
    with transaction.atomic():
        for k, v in clean.items():
            Setting.objects.update_or_create(key=k, defaults={"value": str(v)})
        log_admin(request.user.id, "settings", now, memo=", ".join(f"{SETTINGS_META[k][0]}={v}" for k, v in clean.items()))
    return jres(_settings_json(get_settings()))


# ---------------------------------------------------------------- 통계

@require_GET
@admin_required
def stats(request):
    zone, now = tz(), clock.now()
    date_s = request.GET.get("date")
    try:
        day = datetime.strptime(date_s, "%Y-%m-%d").date() if date_s else datetime.fromtimestamp(now, zone).date()
    except ValueError:
        raise ApiError(400, "BAD_REQUEST", "date는 YYYY-MM-DD 형식이어야 합니다.")
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    return jres({"date": day.isoformat(), "hours": compute_hourly_stats(
        int(start.timestamp()), int((start + timedelta(days=1)).timestamp()), now)})


# ---------------------------------------------------------------- 사전 경고 직접 보내기

@require_POST
@admin_required
def notice(request, user_id):
    """누적 경고와 별개로, 본인 계정에 사전 경고(주의) 알림만 보낸다."""
    body = json_body(request)
    message = str_field(body, "message", 300)
    seat_no = int_field(body, "seat_no", required=False)
    now = clock.now()
    with transaction.atomic():
        u = _get_user(user_id)
        seat = Seat.objects.filter(no=seat_no).first() if seat_no else None
        title = f"{seat.label} 좌석 관련 사전 경고" if seat else "관리자 사전 경고"
        notify(u.id, "prewarn", "warn", title,
               message or "좌석 이용 규정을 지켜 주세요. 계속되면 경고가 부여될 수 있어요.", now, seat=seat)
        log_admin(request.user.id, "notice", now, seat_no=seat.no if seat else None, target_user_id=u.id, memo=message)
    return _ok()


# ---------------------------------------------------------------- 판정 피드백

@require_POST
@admin_required
def feedback(request, no):
    """관리자가 좌석을 직접 확인해 현재 판정이 맞는지 기록. 틀렸으면 올바른 상태로 바로 수정할 수 있다."""
    body = json_body(request)
    verdict = body.get("verdict")
    if verdict not in ("correct", "wrong"):
        raise ApiError(400, "BAD_REQUEST", "verdict는 correct 또는 wrong이어야 합니다.")
    correct = body.get("correct_detail") or None
    if verdict == "wrong" and correct not in ASSIGNABLE:
        raise ApiError(400, "BAD_REQUEST", "틀림이면 올바른 상태(correct_detail)를 골라 주세요.")
    apply = verdict == "wrong" and bool(body.get("apply", True))
    memo = str_field(body, "memo")
    now = clock.now()
    with transaction.atomic():
        it = next((x for x in refresh(now) if x["seat"].no == no), None)
        if it is None:
            raise ApiError(404, "NOT_FOUND", "좌석을 찾을 수 없습니다.")
        seat, j = it["seat"], it["j"]
        if apply and ASSIGNABLE[correct][3] == "in_use" and not (it["res"] and it["res"].status == "in_use"):
            raise ApiError(409, "INVALID_STATE", f"'{DETAILS[correct][1]}'은(는) 이용 중인 예약이 있는 좌석에만 지정할 수 있습니다.")
        fb = JudgmentFeedback.objects.create(
            seat=seat, admin_id=request.user.id, at=now, shown_state=j.seat_state, shown_detail=j.detail,
            source=seat.state_source, verdict=verdict, correct_detail=correct if verdict == "wrong" else None,
            memo=memo, applied=apply)
        if apply:
            state, mark, reason, _ = ASSIGNABLE[correct]
            set_seat_state(seat, state, "manual", now, mark=mark, reason=reason, note=seat.note if state == "unavailable" else None)
            refresh(now)
        shown = f"{SEAT_STATES[j.seat_state]} · {DETAILS[j.detail][1]}"
        log_admin(request.user.id, "feedback", now, seat_no=no,
                  memo=f"{shown} → " + ("맞음" if verdict == "correct" else f"틀림(실제: {DETAILS[correct][1]})"
                                        + (" · 수정함" if apply else "")))
    return _ok(id=fb.id)


@require_GET
@admin_required
def feedback_list(request):
    return jres(analytics.feedback_stats())


# ---------------------------------------------------------------- 샘플 이력 생성

@require_POST
@admin_required
def demo_history(request):
    from ..sample import generate_history
    now = clock.now()
    body = json_body(request)
    weeks = int_field(body, "weeks", required=False) or 4
    if not 1 <= weeks <= 8:
        raise ApiError(400, "BAD_REQUEST", "weeks는 1~8이어야 합니다.")
    n = generate_history(now, weeks=weeks)
    log_admin(request.user.id, "demo_history", now, memo=f"{weeks}주 · 예약 {n}건")
    return _ok(reservations=n)
