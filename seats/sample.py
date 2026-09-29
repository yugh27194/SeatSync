"""샘플 이력 생성: 지난 몇 주 동안의 예약·이용·상태 전이를 그럴듯하게 만들어
'내 이용 기록'과 '혼잡도' 화면을 시연할 수 있게 한다. (오늘은 현재 시각 2시간 전까지만 만든다)

- 테스트 계정(사용자A·B·C, 테스트1~5)은 하루 0~1회 이용 → 내 이용 기록에 표시
- 나머지 수요는 로그인할 수 없는 샘플 이용자(sample01~)로 채운다 → 혼잡도 패턴
- 시간대별 목표 점유율(평일/주말)에 맞춰 좌석마다 이용·휴식·이탈·사석화·연장·미입실을 흉내 낸다
"""
import random
from datetime import datetime, timedelta

from django.contrib.auth.hashers import make_password
from django.db import transaction

from .models import Reservation, ReservationEvent, Seat, StatusLog, User
from .services import get_settings
from .timeutil import tz

REAL_USERS = ["userA", "userB", "userC", "20260001", "20260002", "20260003", "20260004", "20260005"]
SAMPLE_USERS = 36
STEP = 15 * 60  # 빈 좌석에서 새 이용이 시작될지 15분마다 판단

# 시각별 목표 점유율 (0~23시)
WEEKDAY = [0, 0, 0, 0, 0, 0, .10, .15, .30, .45, .55, .55, .40, .50, .75, .80, .78, .70, .50, .55, .65, .62, .45, .25]
WEEKEND = [0, 0, 0, 0, 0, 0, .05, .08, .15, .30, .45, .55, .55, .65, .80, .82, .78, .65, .45, .40, .38, .30, .20, .10]


def _sample_users(now):
    users = []
    for i in range(1, SAMPLE_USERS + 1):
        u, _ = User.objects.get_or_create(
            student_no=f"sample{i:02d}",
            defaults={"name": f"샘플{i:02d}", "password": make_password(None), "is_active": False, "created_at": now})
        users.append(u)
    return users


def generate_history(now, weeks=4, seed=None):
    rnd = random.Random(seed if seed is not None else 20260929 + weeks)
    s = get_settings()
    zone = tz()
    today = datetime.fromtimestamp(now, zone).replace(hour=0, minute=0, second=0, microsecond=0)
    seats = list(Seat.objects.filter(active=True).exclude(state="unavailable"))
    real = list(User.objects.filter(student_no__in=REAL_USERS))
    with transaction.atomic():
        pool = _sample_users(now)
        created = 0
        logs, events = [], []
        for d in range(weeks * 7, -1, -1):  # 오늘은 2시간 전까지만 (현재 좌석 상태와 겹치지 않게)
            day = today - timedelta(days=d)
            target = WEEKEND if day.weekday() >= 5 else WEEKDAY
            busy = {}  # user_id → 그날 이용 끝 시각 (한 사람이 같은 시간에 두 자리 쓰지 않게)
            real_today = [u for u in real if rnd.random() < 0.7]
            rnd.shuffle(real_today)
            for seat in seats:
                t = int((day + timedelta(hours=s.open_hour)).timestamp())
                close = int((day + timedelta(hours=min(s.close_hour, 24))).timestamp())
                if d == 0:
                    close = min(close, now - 2 * 3600)
                while t < close - 3600:
                    occ = target[datetime.fromtimestamp(t, zone).hour]
                    length = rnd.randint(70, 230) * 60
                    idle_mean = length * (1 - occ) / occ if occ > 0 else float("inf")
                    if occ <= 0 or rnd.random() > STEP / max(idle_mean, STEP):
                        t += STEP
                        continue
                    user = _pick_user(rnd, real_today, pool, busy, t)
                    if user is None:
                        t += STEP
                        continue
                    end_t = _session(rnd, s, seat, user, t, min(t + length, close), logs, events)
                    busy[user.id] = end_t
                    created += 1
                    t = end_t + rnd.randint(1, 4) * STEP
        ReservationEvent.objects.bulk_create(events, batch_size=500)
        StatusLog.objects.bulk_create(logs, batch_size=500)
    return created


def _pick_user(rnd, real_today, pool, busy, t):
    # 테스트 계정은 하루 한 번까지, 나머지는 샘플 이용자
    for u in list(real_today):
        if busy.get(u.id, 0) <= t:
            real_today.remove(u)
            return u
    free = [u for u in pool if busy.get(u.id, 0) <= t]
    return rnd.choice(free) if free else None


def _session(rnd, s, seat, user, start, end, logs, events):
    """한 번의 예약·이용을 만들고 끝난 시각을 돌려준다."""
    use_min = s.sec("default_use_min")
    # 끝난 상태로 한 번만 저장한다. 'reserved'로 먼저 저장하면 지금 예약 중인 좌석·사용자와 겹쳐
    # "좌석/사용자당 진행 중 예약 1건" 제약(ux_res_active_*)에 걸린다.
    r = Reservation(user=user, seat=seat, status="reserved", start_at=start, end_at=start + use_min,
                    source=rnd.choice(["map", "map", "seat_page"]))
    ev = [("reserve", start, None)]
    log = [(start, "in_use", "waiting")]

    if rnd.random() < 0.07:  # 미입실
        t_end = start + s.sec("checkin_limit_min") + 60
        r.status, r.ended_at = "no_show", t_end
        ev.append(("no_show", t_end, None))
        log.append((t_end, "available", "empty"))
        _flush(r, seat, ev, log, logs, events)
        return t_end

    t = start + rnd.randint(1, 10) * 60
    r.checked_in_at = t
    ev.append(("checkin", t, None))
    log.append((t, "in_use", "using"))
    status, issue = "returned", False
    # 이용 중 휴식: 잠시 자리 비움 / 짐만 두고 비움 (일부는 기준 초과 → 이탈·사석화)
    while True:
        t += rnd.randint(40, 90) * 60
        if t >= end - 20 * 60 or rnd.random() < 0.45:
            break
        if rnd.random() < 0.55:
            detail, issue_detail, limit = "away_short", "away", s.sec("away_limit_min")
        else:
            detail, issue_detail, limit = "item", "hoarding", s.sec("hoarding_min")
        brk = rnd.randint(5, 25) * 60 if rnd.random() < 0.8 else limit + rnd.randint(10, 40) * 60
        log.append((t, "in_use", detail))
        if brk > limit:
            log.append((t + limit, "in_use", issue_detail))
            issue = True
            if rnd.random() < 0.4:  # 관리자가 강제 반납
                t += brk
                status = "force_returned"
                break
        t += brk
        log.append((t, "in_use", "using"))
    if status != "force_returned":
        if end - start > use_min and rnd.random() < 0.6:  # 연장
            ext_at = r.end_at - rnd.randint(5, 25) * 60
            r.end_at += s.sec("extend_min")
            r.extend_count = 1
            ev.append(("extend", ext_at, None))
        t = max(t, min(end, r.end_at))
        if t >= r.end_at:
            t, status = r.end_at, "expired"
    r.status, r.ended_at = status, t
    ev.append(({"returned": "return", "expired": "expire", "force_returned": "force_return"}[status], t,
               "이탈·사석화" if status == "force_returned" and issue else None))
    log.append((t, "available", "empty"))
    _flush(r, seat, ev, log, logs, events)
    return t


def _flush(r, seat, ev, log, logs, events):
    r.save()
    for kind, at, memo in ev:
        events.append(ReservationEvent(reservation=r, user_id=r.user_id, kind=kind, at=at, memo=memo))
    for at, st, det in log:
        logs.append(StatusLog(seat_no=seat.no, seat_state=st, detail=det, reservation_id=r.id, at=at))
