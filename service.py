"""판정 갱신 파이프라인(§5.4)과 예약 공통 로직."""
from db import get_settings, tx
from status import (OFFLINE, RETURN_DUE, STATE_ALERT_TYPES, STATE_META, Detection, Reservation, judge)
from timeutil import to_iso

ACTIVE = ("reserved", "in_use")


# ---------------------------------------------------------------- 조회 헬퍼

def active_reservations(conn):
    """seat_no → 활성 예약(dict, 예약자 이름·학번 포함)."""
    rows = conn.execute(
        """SELECT r.*, u.name AS user_name, u.student_no AS student_no
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


def to_det(row):
    if row is None:
        return None
    return Detection(occupancy=row["occupancy"], since=row["since"], updated_at=row["updated_at"])


# ---------------------------------------------------------------- 파이프라인

def _sweep(conn, now, s):
    limit = s.checkin_limit_min * 60
    no_shows = conn.execute(
        "SELECT id, seat_no FROM reservations WHERE status = 'reserved' AND ? > start_at + ?", (now, limit)
    ).fetchall()
    for r in no_shows:
        conn.execute("UPDATE reservations SET status='no_show', ended_at=? WHERE id=?", (now, r["id"]))
        # 정보성 알림. 같은 좌석의 미해결 no_show가 있으면 중복 생성하지 않는다.
        conn.execute(
            "INSERT OR IGNORE INTO alerts(seat_no, type, reservation_id, created_at) VALUES (?, 'no_show', ?, ?)",
            (r["seat_no"], r["id"], now),
        )
    conn.execute(
        "UPDATE reservations SET status='expired', ended_at=? WHERE status IN ('reserved','in_use') AND ? >= end_at",
        (now, now),
    )


def _judge_seats(conn, now, s, only=None):
    seats = conn.execute("SELECT * FROM seats WHERE active = 1 ORDER BY no").fetchall()
    res_map = active_reservations(conn)
    det_map = {r["seat_no"]: dict(r) for r in conn.execute("SELECT * FROM detections").fetchall()}
    out = []
    for seat in seats:
        if only is not None and seat["no"] not in only:
            continue
        res = res_map.get(seat["no"])
        det = det_map.get(seat["no"])
        out.append({"seat": dict(seat), "res": res, "det": det,
                    "j": judge(to_res(res), to_det(det), now, s)})
    return out


def _record(conn, item, now):
    """상태 전이 기록 + 알림 생성/자동 해소."""
    seat_no, j, res, det = item["seat"]["no"], item["j"], item["res"], item["det"]
    prev = conn.execute("SELECT state FROM seat_state WHERE seat_no = ?", (seat_no,)).fetchone()
    prev_state = prev["state"] if prev else None
    changed = prev_state != j.state
    if changed:
        conn.execute(
            "INSERT INTO status_log(seat_no, state, prev_state, reservation_id, occupancy, at) VALUES (?,?,?,?,?,?)",
            (seat_no, j.state, prev_state, res["id"] if res else None, det["occupancy"] if det else None, now),
        )
        conn.execute(
            "INSERT INTO seat_state(seat_no, state, since) VALUES (?,?,?) "
            "ON CONFLICT(seat_no) DO UPDATE SET state = excluded.state, since = excluded.since",
            (seat_no, j.state, j.since),
        )

    # SPEC-ASSUMPTION: 감지 끊김(OFFLINE)은 실제 상태를 모르는 것이므로 기존 알림을 자동 해소하지 않는다.
    if j.state == OFFLINE:
        return
    atype = STATE_META[j.state]["alert"]
    # 알림은 해당 상태로 '전이'할 때만 만든다 — 관리자가 처리 완료한 뒤 같은 상태가 이어져도 다시 뜨지 않게.
    if atype and changed:
        conn.execute(
            "INSERT OR IGNORE INTO alerts(seat_no, type, reservation_id, created_at) VALUES (?,?,?,?)",
            (seat_no, atype, res["id"] if res else None, now),
        )
    stale_types = [t for t in STATE_ALERT_TYPES if t != atype]
    marks = ",".join("?" * len(stale_types))
    conn.execute(
        f"UPDATE alerts SET resolved_at = ?, resolution = 'auto' "
        f"WHERE seat_no = ? AND resolved_at IS NULL AND type IN ({marks})",
        (now, seat_no, *stale_types),
    )


def refresh(conn, now):
    """sweep → 전 좌석 판정 → 전이 기록 → 알림 → (옵션) 자동 반납. 좌석별 판정 결과 목록을 돌려준다."""
    with tx(conn):
        s = get_settings(conn)
        _sweep(conn, now, s)
        results = _judge_seats(conn, now, s)
        for item in results:
            _record(conn, item, now)

        if s.auto_return_empty:
            returned = []
            for item in results:
                if item["j"].state == RETURN_DUE and item["res"]:
                    conn.execute(
                        "UPDATE reservations SET status='returned', ended_at=? WHERE id=? AND status IN ('reserved','in_use')",
                        (now, item["res"]["id"]),
                    )
                    returned.append(item["seat"]["no"])
            if returned:
                redo = {i["seat"]["no"]: i for i in _judge_seats(conn, now, s, only=set(returned))}
                for item in redo.values():
                    _record(conn, item, now)
                results = [redo.get(i["seat"]["no"], i) for i in results]
    return results


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
