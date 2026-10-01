"""이용 기록·혼잡도·판정 정확도 집계. 시각 계산은 모두 표시용 타임존(KST) 기준."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .models import JudgmentFeedback, Reservation, ReservationEvent, Seat, StatusLog
from .status import DETAILS, SEAT_STATES
from .timeutil import to_iso, tz

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
RES_STATUS_LABELS = {"reserved": "예약(입실 전)", "in_use": "이용 중", "returned": "반납", "cancelled": "예약 취소",
                     "expired": "시간 만료", "no_show": "미입실", "force_returned": "강제 반납"}


def _dt(epoch):
    return datetime.fromtimestamp(epoch, tz())


def _ts(dt):
    return int(dt.timestamp())


def _day_start(epoch):
    d = _dt(epoch)
    return datetime(d.year, d.month, d.day, tzinfo=tz())


# ---------------------------------------------------------------- 내 이용 기록

def usage_interval(r, now):
    """실제 이용 구간(체크인 ~ 반납/만료/현재). 체크인하지 않았으면 None."""
    if not r.checked_in_at:
        return None
    end = r.ended_at if r.ended_at else now
    end = min(end, r.end_at)
    return (r.checked_in_at, end) if end > r.checked_in_at else None


def _buckets(period, now, count):
    """[(시작, 끝, 라벨)] 최근 count개. day=일, week=월요일 시작 주, month=달."""
    today = _day_start(now)
    out = []
    if period == "day":
        for i in range(count - 1, -1, -1):
            d = today - timedelta(days=i)
            out.append((d, d + timedelta(days=1), f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"))
    elif period == "week":
        monday = today - timedelta(days=today.weekday())
        for i in range(count - 1, -1, -1):
            d = monday - timedelta(weeks=i)
            out.append((d, d + timedelta(weeks=1), f"{d.month}/{d.day}~"))
    else:
        y, m = today.year, today.month
        months = []
        for _ in range(count):
            months.append((y, m))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        for y, m in reversed(months):
            start = datetime(y, m, 1, tzinfo=tz())
            end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=tz())
            out.append((start, end, f"{y}.{m:02d}"))
    return [(_ts(a), _ts(b), label) for a, b, label in out]


PERIOD_COUNTS = {"day": 14, "week": 8, "month": 6}


def user_history(user, period, now):
    period = period if period in PERIOD_COUNTS else "day"
    buckets = _buckets(period, now, PERIOD_COUNTS[period])
    lo, hi = buckets[0][0], buckets[-1][1]
    reservations = list(Reservation.objects.filter(user=user, start_at__lt=hi).exclude(ended_at__lt=lo))
    mins = [0.0] * len(buckets)
    counted = [0] * len(buckets)
    for r in reservations:
        iv = usage_interval(r, now)
        if not iv:
            continue
        for i, (a, b, _) in enumerate(buckets):
            overlap = min(iv[1], b) - max(iv[0], a)
            if overlap > 0:
                mins[i] += overlap / 60
                counted[i] += 1
    in_range = [r for r in reservations if lo <= r.start_at < hi]
    events = Counter(ReservationEvent.objects.filter(user=user, at__gte=lo, at__lt=hi).values_list("kind", flat=True))
    status = Counter(r.status for r in in_range)
    total = sum(mins)
    used_buckets = sum(1 for m in mins if m > 0)
    lengths = [iv[1] - iv[0] for iv in (usage_interval(r, now) for r in in_range) if iv]
    return {
        "period": period,
        "buckets": [{"label": label, "start": to_iso(a), "minutes": round(m), "sessions": c}
                    for (a, _b, label), m, c in zip(buckets, mins, counted)],
        "summary": {
            "total_min": round(total),
            "avg_min": round(total / used_buckets) if used_buckets else 0,  # 이용한 날(주·월) 평균
            "active_buckets": used_buckets,
            "reservations": len(in_range),
            "extends": events["extend"] + events["admin_extend"],
            "returns": status["returned"],
            "expired": status["expired"],
            "no_shows": status["no_show"],
            "force_returns": status["force_returned"],
            "cancels": status["cancelled"],
            "longest_min": round(max(lengths) / 60) if lengths else 0,
        },
    }


def user_reservations(user, now, limit=30):
    rows = (Reservation.objects.filter(user=user).select_related("seat").prefetch_related("events")
            .order_by("-start_at", "-id")[:limit])
    out = []
    for r in rows:
        iv = usage_interval(r, now)
        evs = sorted(r.events.all(), key=lambda e: (e.at, e.id))
        out.append({
            "id": r.id, "seat_label": r.seat.label, "zone": r.seat.zone, "status": r.status,
            "status_label": RES_STATUS_LABELS.get(r.status, r.status),
            "start_at": to_iso(r.start_at), "end_at": to_iso(r.end_at), "checked_in_at": to_iso(r.checked_in_at),
            "ended_at": to_iso(r.ended_at), "used_min": round((iv[1] - iv[0]) / 60) if iv else 0,
            "extend_count": sum(1 for e in evs if e.kind in ("extend", "admin_extend")),
            "events": [{"kind": e.kind, "label": ReservationEvent.KINDS.get(e.kind, e.kind), "at": to_iso(e.at),
                        "memo": e.memo} for e in evs],
        })
    return out


# ---------------------------------------------------------------- 혼잡도·실사용률

# 세부 상태 → 지표: occupied(좌석을 차지함), actual(사람이 실제로 앉아 있음), idle(차지만 하고 비어 있음)
ACTUAL_USE = {"using", "detected", "seated_unchecked", "no_checkin"}
IDLE = {"waiting", "away_short", "item", "away", "hoarding"}


def _segments(seat_no, lo, hi):
    """[lo, hi) 구간의 (시작, 끝, 좌석 상태, 세부 상태) 목록."""
    before = StatusLog.objects.filter(seat_no=seat_no, at__lt=lo).order_by("-at", "-id").first()
    logs = list(StatusLog.objects.filter(seat_no=seat_no, at__gte=lo, at__lt=hi).order_by("at", "id")
                .values_list("at", "seat_state", "detail"))
    points = ([(lo, before.seat_state, before.detail)] if before else []) + logs
    for i, (t0, st, det) in enumerate(points):
        t1 = points[i + 1][0] if i + 1 < len(points) else hi
        if t1 > t0:
            yield t0, t1, st, det


def _split_hours(t0, t1):
    """구간을 시각 경계로 나눠 (그 시각의 시작 epoch, 초) 목록."""
    t = t0
    while t < t1:
        d = _dt(t)
        hour_start = _ts(datetime(d.year, d.month, d.day, d.hour, tzinfo=tz()))
        seg = min(t1, hour_start + 3600) - t
        yield hour_start, seg
        t += seg


def congestion(now, s, weeks=4):
    """요일×시간 평균 점유율·실사용률·유휴 점유율, 오늘 시간대별 추이, 현재 상태."""
    seats = list(Seat.objects.filter(active=True).values_list("no", flat=True))
    n = max(1, len(seats))
    hours = list(range(s.open_hour, s.close_hour))
    today = _day_start(now)
    lo = _ts(today - timedelta(weeks=weeks))
    occ = defaultdict(float)
    act = defaultdict(float)
    idle = defaultdict(float)
    issue = defaultdict(float)
    per_hour = defaultdict(lambda: [0.0, 0.0])  # 시각 epoch → [점유, 실사용] (오늘 추이용)
    for no in seats:
        for t0, t1, st, det in _segments(no, lo, now):
            if st != "in_use":
                continue
            for hs, sec in _split_hours(t0, t1):
                d = _dt(hs)
                key = (d.weekday(), d.hour)
                occ[key] += sec
                if det in ACTUAL_USE:
                    act[key] += sec
                    per_hour[hs][1] += sec
                if det in IDLE:
                    idle[key] += sec
                if DETAILS[det][2]:
                    issue[key] += sec
                per_hour[hs][0] += sec
    # 분모: 기간 안에서 각 (요일, 시각) 슬롯이 지나간 시간(초) × 좌석 수
    slot_sec = defaultdict(float)
    for hs, sec in _split_hours(lo, now):
        d = _dt(hs)
        slot_sec[(d.weekday(), d.hour)] += sec

    def grid(src):
        return [[round(src[(wd, h)] / (slot_sec[(wd, h)] * n), 3) if slot_sec[(wd, h)] else None for h in hours]
                for wd in range(7)]

    today_rows = []
    for h in hours:
        hs = _ts(today + timedelta(hours=h))
        if hs >= now:
            today_rows.append({"hour": h, "occupancy": None, "actual": None})
            continue
        span = min(3600, now - hs) * n
        o, a = per_hour.get(hs, (0.0, 0.0))
        today_rows.append({"hour": h, "occupancy": round(o / span, 3), "actual": round(a / span, 3)})
    occ_grid = grid(occ)
    flat = [(occ_grid[wd][i], wd, h) for wd in range(7) for i, h in enumerate(hours) if occ_grid[wd][i] is not None]
    peak = max(flat) if flat else None
    quiet = min(flat) if flat else None
    return {
        "weeks": weeks, "days": WEEKDAYS, "hours": hours, "seats": n,
        "occupancy": occ_grid, "actual": grid(act), "idle": grid(idle), "issue": grid(issue),
        "today": today_rows,
        "peak": {"weekday": WEEKDAYS[peak[1]], "hour": peak[2], "rate": peak[0]} if peak else None,
        "quiet": {"weekday": WEEKDAYS[quiet[1]], "hour": quiet[2], "rate": quiet[0]} if quiet else None,
    }


def live_usage(results):
    """현재 좌석 상태 요약: 점유율(사용중 좌석 비율)과 실사용률(실제 앉아 있는 비율)."""
    total = len(results)
    usable = sum(1 for it in results if it["j"].seat_state != "unavailable")
    in_use = sum(1 for it in results if it["j"].seat_state == "in_use")
    actual = sum(1 for it in results if it["j"].detail in ACTUAL_USE)
    return {"total": total, "usable": usable, "in_use": in_use, "available": usable - in_use, "actual": actual,
            "occupancy": round(in_use / usable, 3) if usable else 0.0,
            "actual_rate": round(actual / usable, 3) if usable else 0.0}


# ---------------------------------------------------------------- 판정 정확도

def feedback_stats(limit=20):
    rows = list(JudgmentFeedback.objects.select_related("seat", "admin").order_by("-at", "-id"))

    def acc(items):
        c = sum(1 for f in items if f.verdict == "correct")
        return {"total": len(items), "correct": c, "accuracy": round(c / len(items), 3) if items else None}

    by_source = defaultdict(list)
    by_state = defaultdict(list)
    for f in rows:
        by_source[f.source].append(f)
        by_state[f.shown_state].append(f)
    confusion = Counter((f.shown_detail, f.correct_detail) for f in rows if f.verdict == "wrong" and f.correct_detail)
    return {
        "overall": acc(rows),
        "by_source": {k: acc(v) for k, v in by_source.items()},
        "by_state": {SEAT_STATES.get(k, k): acc(v) for k, v in by_state.items()},
        "confusion": [{"shown": DETAILS.get(a, (None, a))[1], "correct": DETAILS.get(b, (None, b))[1], "count": c}
                      for (a, b), c in confusion.most_common(8)],
        "recent": [{
            "id": f.id, "at": to_iso(f.at), "seat_label": f.seat.label, "admin_name": f.admin.name if f.admin else None,
            "shown": f"{SEAT_STATES.get(f.shown_state, f.shown_state)} · {DETAILS.get(f.shown_detail, (None, f.shown_detail))[1]}",
            "source": f.source, "verdict": f.verdict,
            "correct": DETAILS[f.correct_detail][1] if f.correct_detail in DETAILS else None,
            "memo": f.memo, "applied": f.applied, "cam_state": f.cam_state, "cam_confidence": f.cam_confidence,
        } for f in rows[:limit]],
        # 카메라 판정 좌석의 사람 탐지 점수: 맞음/틀림 평균 (--confidence 임계값 조정 참고용)
        "camera_confidence": {v: (round(sum(xs) / len(xs), 3) if xs else None) for v, xs in {
            "correct": [f.cam_confidence for f in rows if f.source == "camera" and f.verdict == "correct" and f.cam_confidence is not None],
            "wrong": [f.cam_confidence for f in rows if f.source == "camera" and f.verdict == "wrong" and f.cam_confidence is not None],
        }.items()},
    }
