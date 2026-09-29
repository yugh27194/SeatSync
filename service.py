"""판정 갱신 파이프라인과 예약·좌석·관리자 공통 로직."""
from db import get_settings, tx
from status import ACTUAL_STATES, ISSUES, OK, Actual, Reservation, judge
from timeutil import to_iso

ACTIVE = ("reserved", "in_use")


# ---------------------------------------------------------------- 조회 헬퍼

def active_reservations(conn):
    """seat_no → 활성 예약(dict, 예약자 정보 포함)."""
    rows = conn.execute(
        """SELECT r.*, u.name AS user_name, u.student_no AS student_no, u.warnings AS user_warnings,
                  u.suspended_until AS user_suspended_until
             FROM reservations r JOIN users u ON u.id = r.user_id
            WHERE r.status IN ('reserved','in_use')"""
    ).fetchall()
    return {r["seat_no"]: dict(r) for r in rows}


def user_active_reservation(conn, user_id):
    row = conn.execute(
        """SELECT r.*, s.label AS seat_label FROM reservations r JOIN seats s ON s.no = r.seat_no
            WHERE r.user_id = ? AND r.status IN ('reserved','in_use')""",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def to_res(row):
    if row is None:
        return None
    return Reservation(id=row["id"], status=row["status"], start_at=row["start_at"],
                       end_at=row["end_at"], checked_in_at=row["checked_in_at"])


def to_actual(seat):
    return Actual(state=seat["state"], since=seat["state_since"])


# ---------------------------------------------------------------- 파이프라인

def _sweep(conn, now, s):
    limit = s.checkin_limit_min * 60
    no_shows = conn.execute(
        "SELECT id, seat_no FROM reservations WHERE status = 'reserved' AND ? > start_at + ?", (now, limit)
    ).fetchall()
    for r in no_shows:
        conn.execute("UPDATE reservations SET status='no_show', ended_at=? WHERE id=?", (now, r["id"]))
        conn.execute(
            "INSERT OR IGNORE INTO alerts(seat_no, type, reservation_id, created_at) VALUES (?, 'no_show', ?, ?)",
            (r["seat_no"], r["id"], now),
        )
    conn.execute(
        "UPDATE reservations SET status='expired', ended_at=? WHERE status IN ('reserved','in_use') AND ? >= end_at",
        (now, now),
    )


def _judge_seats(conn, now, s):
    seats = conn.execute("SELECT * FROM seats WHERE active = 1 ORDER BY no").fetchall()
    res_map = active_reservations(conn)
    out = []
    for seat in seats:
        res = res_map.get(seat["no"])
        out.append({"seat": dict(seat), "res": res, "j": judge(to_res(res), to_actual(seat), now, s)})
    return out


def _record(conn, item, now):
    """상태·상황 전이 기록 + 확인 필요 알림 생성/자동 해소."""
    seat, j, res = item["seat"], item["j"], item["res"]
    seat_no = seat["no"]
    prev = conn.execute("SELECT seat_state, situation FROM seat_state WHERE seat_no = ?", (seat_no,)).fetchone()
    changed = prev is None or (prev["seat_state"], prev["situation"]) != (j.seat_state, j.situation)
    if changed:
        conn.execute(
            "INSERT INTO status_log(seat_no, seat_state, situation, prev_situation, reservation_id, actual, at) "
            "VALUES (?,?,?,?,?,?,?)",
            (seat_no, j.seat_state, j.situation, prev["situation"] if prev else None,
             res["id"] if res else None, seat["state"], now),
        )
        conn.execute(
            "INSERT INTO seat_state(seat_no, seat_state, situation, since) VALUES (?,?,?,?) "
            "ON CONFLICT(seat_no) DO UPDATE SET seat_state=excluded.seat_state, situation=excluded.situation, "
            "since=excluded.since",
            (seat_no, j.seat_state, j.situation, j.since),
        )
    # 알림은 확인 필요 상황으로 '전이'할 때만 만든다 — 처리 완료 후 같은 상황이 이어져도 다시 뜨지 않게.
    if j.situation != OK and (prev is None or prev["situation"] != j.situation):
        conn.execute(
            "INSERT OR IGNORE INTO alerts(seat_no, type, reservation_id, created_at) VALUES (?,?,?,?)",
            (seat_no, j.situation, res["id"] if res else None, now),
        )
    stale = [t for t in ISSUES if t != j.situation]
    conn.execute(
        f"UPDATE alerts SET resolved_at=?, resolution='auto' WHERE seat_no=? AND resolved_at IS NULL "
        f"AND type IN ({','.join('?' * len(stale))})",
        (now, seat_no, *stale),
    )


def refresh(conn, now):
    """sweep → 전 좌석 판정·대조 → 전이 기록 → 알림. 좌석별 결과 목록을 돌려준다."""
    with tx(conn):
        s = get_settings(conn)
        _sweep(conn, now, s)
        results = _judge_seats(conn, now, s)
        for item in results:
            _record(conn, item, now)
    return results


# ---------------------------------------------------------------- 좌석 현장 상태

def set_seat_state(conn, seat_no, state, source, now, note=None, keep_note=False):
    """현장 상태 변경. 같은 상태면 시작 시각을 유지한다."""
    row = conn.execute("SELECT state, state_note FROM seats WHERE no=?", (seat_no,)).fetchone()
    if row is None:
        return False
    if state != "unavailable" and not keep_note:
        note = None
    elif keep_note and note is None:
        note = row["state_note"]
    if row["state"] == state:
        conn.execute("UPDATE seats SET state_source=?, state_note=? WHERE no=?", (source, note, seat_no))
        return False
    conn.execute("UPDATE seats SET state=?, state_since=?, state_source=?, state_note=? WHERE no=?",
                 (state, now, source, note, seat_no))
    return True


# ---------------------------------------------------------------- 예약 공통

def extend_check(res, now, s):
    """(연장 가능 여부, 불가 사유)."""
    if res is None or res["status"] != "in_use":
        return False, "체크인한 뒤 이용 중일 때만 연장할 수 있어요."
    if res["extend_count"] >= s.max_extends:
        return False, f"연장은 최대 {s.max_extends}회까지 가능해요."
    if res["end_at"] - now > s.extend_window_min * 60:
        return False, f"종료 {s.extend_window_min}분 전부터 연장할 수 있어요."
    return True, None


def reservation_json(res, now, s, seat_label=None):
    if res is None:
        return None
    ok, reason = extend_check(res, now, s)
    return {
        "id": res["id"],
        "seat_no": res["seat_no"],
        "seat_label": seat_label or res.get("seat_label"),
        "status": res["status"],
        "start_at": to_iso(res["start_at"]),
        "end_at": to_iso(res["end_at"]),
        "checked_in_at": to_iso(res["checked_in_at"]),
        "checkin_deadline": to_iso(res["start_at"] + s.checkin_limit_min * 60) if res["status"] == "reserved" else None,
        "can_extend": ok,
        "extend_reason": reason,
        "extend_count": res["extend_count"],
        "max_extends": s.max_extends,
        "remaining_sec": max(0, res["end_at"] - now),
    }


def suspension(user_row, now):
    """정지 중이면 종료 시각(epoch), 아니면 None."""
    until = user_row["suspended_until"]
    return until if until and until > now else None


# ---------------------------------------------------------------- 관리자 이력

def log_admin(conn, admin_id, action, now, seat_no=None, reservation_id=None, target_user_id=None,
              alert_id=None, memo=None):
    conn.execute(
        "INSERT INTO admin_log(admin_id, action, seat_no, reservation_id, target_user_id, alert_id, memo, at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (admin_id, action, seat_no, reservation_id, target_user_id, alert_id, memo, now),
    )


# ---------------------------------------------------------------- 시연 상황 배치

# (좌석 번호, 예약자 아이디 또는 None, 예약 상태, 예약 시작(분 전), 현장 상태, 현장 상태 시작(분 전), 메모)
DEMO_LAYOUT = [
    (1, "userA",    "in_use",   30, "occupied",    30, "정상 이용"),
    (2, "userB",    "in_use",   50, "empty",       40, "이탈"),
    (3, None,       None,        0, "occupied",    10, "무단 점유"),
    (4, "userC",    "reserved",  5, "occupied",     3, "체크인 누락"),
    (5, None,       None,        0, "unavailable", 60, "사용불가(의자 파손)"),
    (6, "20260001", "reserved",  3, "unavailable",  1, "예약 좌석 사용불가"),
    (7, "20260002", "in_use",   60, "item",        40, "사석화"),
    (8, "20260003", "reserved", None, "empty",      30, "미입실 → 자동 취소"),
]


def setup_demo(conn, now):
    """활성 예약·미해결 알림을 정리하고 DEMO_LAYOUT대로 다양한 상황을 만든다. 안내 문구 목록을 돌려준다."""
    msgs = []
    with tx(conn):
        s = get_settings(conn)
        conn.execute("UPDATE reservations SET status='cancelled', ended_at=? WHERE status IN ('reserved','in_use')",
                     (now,))
        conn.execute("UPDATE alerts SET resolved_at=?, resolution='reset' WHERE resolved_at IS NULL", (now,))
        # 상황 캐시를 비워 같은 상황이라도 알림이 새로 생기게 한다
        conn.execute("DELETE FROM seat_state")
        valid = {r["no"]: r["label"] for r in conn.execute("SELECT no, label FROM seats WHERE active=1")}
        for seat_no, sno, status, start_ago, state, state_ago, memo in DEMO_LAYOUT:
            if seat_no not in valid:
                continue
            note = {5: "의자 파손", 6: "조명 고장"}.get(seat_no) if state == "unavailable" else None
            conn.execute("UPDATE seats SET state=?, state_since=?, state_source='manual', state_note=? WHERE no=?",
                         (state, now - state_ago * 60, note, seat_no))
            if sno:
                user = conn.execute("SELECT id, name FROM users WHERE student_no=?", (sno,)).fetchone()
                if user is None:
                    continue
                if start_ago is None:  # 체크인 제한을 넘긴 예약 → 다음 refresh에서 미입실 처리
                    start_ago = s.checkin_limit_min + 5
                start = now - start_ago * 60
                conn.execute(
                    "INSERT INTO reservations(user_id, seat_no, status, start_at, end_at, checked_in_at, source) "
                    "VALUES (?,?,?,?,?,?,'map')",
                    (user["id"], seat_no, status, start, start + s.default_use_min * 60,
                     start if status == "in_use" else None),
                )
                msgs.append(f"{valid[seat_no]}: {user['name']} {'이용 중' if status == 'in_use' else '예약'} · "
                            f"현장 {ACTUAL_STATES[state]} → {memo}")
            else:
                msgs.append(f"{valid[seat_no]}: 예약 없음 · 현장 {ACTUAL_STATES[state]} → {memo}")
        refresh(conn, now)
    return msgs
