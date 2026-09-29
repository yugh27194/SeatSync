"""로그인/로그아웃/회원가입과 관리자 모드.

관리자 계정은 따로 두지 않는다. 로그인한 사용자가 관리자 기능에 접근하면 "관리자 권한이 필요합니다."를 안내하고,
관리자 코드(SEATSYNC_ADMIN_CODE)를 입력하면 그 세션에서 관리자 권한이 해금된다.
"""
import hmac
import sqlite3
import threading
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, current_app, g, jsonify, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db, tx
from routes import error_response, json_body, now_ts
from service import log_admin

bp = Blueprint("auth", __name__)

ADMIN_REQUIRED_MSG = "관리자 권한이 필요합니다."
MAX_FAILS = 5         # 연속 실패 허용 횟수
LOCK_SEC = 5 * 60     # 초과 시 잠금 시간

# 사용자별 관리자 코드 실패 기록 {user_id: [실패 횟수, 잠금 해제 시각]}.
# 세션 쿠키를 지워도 우회되지 않게 서버 메모리에 둔다(단일 프로세스 가정).
_fails = {}
_fails_lock = threading.Lock()


def _is_api():
    return request.path.startswith("/api/")


def load_user():
    g.user = None
    g.admin = False
    uid = session.get("uid")
    if uid is None:
        return
    row = get_db().execute("SELECT id, student_no, name FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        session.clear()
        return
    g.user = dict(row)
    g.admin = session.get("admin_until", 0) > now_ts()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if _is_api():
                return error_response(401, "UNAUTHENTICATED", "로그인이 필요합니다.")
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """관리자 모드가 아니면 API는 403 ADMIN_REQUIRED, 화면은 관리자 코드 입력 화면."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.admin:
            if _is_api():
                return error_response(403, "ADMIN_REQUIRED", ADMIN_REQUIRED_MSG)
            return render_template("admin_unlock.html", next_url=request.full_path.rstrip("?"),
                                   error=None, **_lock_info()), 403
        # 사용할 때마다 유지 시간을 연장한다(마지막 사용 기준 만료)
        session["admin_until"] = now_ts() + current_app.config["ADMIN_MODE_MIN"] * 60
        return view(*args, **kwargs)
    return wrapped


def _lock_info():
    with _fails_lock:
        cnt, until = _fails.get(g.user["id"], [0, 0])
    now = now_ts()
    return {"locked_sec": max(0, until - now), "remaining": max(0, MAX_FAILS - cnt) if until <= now else 0}


def try_unlock(code):
    """(성공 여부, 에러 코드, 메시지)"""
    uid, now = g.user["id"], now_ts()
    with _fails_lock:
        cnt, until = _fails.get(uid, [0, 0])
        if until > now:
            mins = (until - now + 59) // 60
            return False, "ADMIN_LOCKED", f"시도 횟수를 초과했습니다. {mins}분 후 다시 시도하세요."
        if until:  # 잠금이 끝났으면 초기화
            cnt, until = 0, 0
        expected = current_app.config["ADMIN_CODE"]
        if isinstance(code, str) and code and hmac.compare_digest(code.encode(), expected.encode()):
            _fails.pop(uid, None)
            ok = True
        else:
            cnt += 1
            if cnt >= MAX_FAILS:
                _fails[uid] = [cnt, now + LOCK_SEC]
            else:
                _fails[uid] = [cnt, 0]
            ok = False
    db = get_db()
    if ok:
        session["admin_until"] = now + current_app.config["ADMIN_MODE_MIN"] * 60
        g.admin = True
        with tx(db):
            log_admin(db, uid, "admin_on", now)
        return True, None, None
    if cnt >= MAX_FAILS:
        with tx(db):
            log_admin(db, uid, "admin_locked", now, memo=f"관리자 코드 {MAX_FAILS}회 실패")
        return False, "ADMIN_LOCKED", f"관리자 코드가 {MAX_FAILS}회 틀렸습니다. {LOCK_SEC // 60}분 후 다시 시도하세요."
    return False, "BAD_ADMIN_CODE", f"관리자 코드가 올바르지 않습니다. (남은 시도 {MAX_FAILS - cnt}회)"


def lock_admin():
    if session.pop("admin_until", None) and g.user:
        db = get_db()
        with tx(db):
            log_admin(db, g.user["id"], "admin_off", now_ts())
    g.admin = False


def _safe_next(target):
    """오픈 리다이렉트 방지: 같은 사이트 내부 경로만 허용."""
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


# ---------------------------------------------------------------- 로그인·회원가입

@bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next(request.values.get("next"))
    error = None
    if request.method == "POST":
        student_no = (request.form.get("student_no") or "").strip()
        password = request.form.get("password") or ""
        row = get_db().execute("SELECT * FROM users WHERE student_no = ?", (student_no,)).fetchone()
        if row and check_password_hash(row["pw_hash"], password):
            session.clear()
            session["uid"] = row["id"]
            session.permanent = True
            return redirect(next_url or url_for("pages.map_page"))
        error = "학번 또는 비밀번호가 올바르지 않습니다."
    elif g.user:
        return redirect(next_url or url_for("pages.map_page"))
    return render_template("login.html", error=error, next_url=next_url or "")


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    next_url = _safe_next(request.values.get("next"))
    error = None
    form = {"student_no": "", "name": ""}
    if request.method == "POST":
        form["student_no"] = student_no = (request.form.get("student_no") or "").strip()
        form["name"] = name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        if not student_no or not name:
            error = "학번과 이름을 입력해 주세요."
        elif len(password) < 4:
            error = "비밀번호는 4자 이상이어야 합니다."
        else:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO users(student_no, name, pw_hash, role, created_at) VALUES (?,?,?,'user',?)",
                    (student_no, name, generate_password_hash(password), now_ts()),
                )
            except sqlite3.IntegrityError:
                error = "이미 가입된 학번입니다."
            else:
                session.clear()
                session["uid"] = cur.lastrowid
                session.permanent = True
                return redirect(next_url or url_for("pages.map_page"))
    return render_template("signup.html", error=error, form=form, next_url=next_url or "")


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()  # 관리자 모드도 함께 해제된다
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------- 관리자 모드

@bp.post("/admin/unlock")
@login_required
def unlock_page():
    next_url = _safe_next(request.form.get("next")) or url_for("pages.admin")
    ok, _code, msg = try_unlock(request.form.get("code", ""))
    if ok:
        return redirect(next_url)
    return render_template("admin_unlock.html", next_url=next_url, error=msg, **_lock_info()), 403


@bp.post("/admin/lock")
@login_required
def lock_page():
    lock_admin()
    return redirect(url_for("pages.map_page"))


@bp.get("/api/admin-mode")
@login_required
def admin_mode_status():
    return jsonify({"admin": g.admin, "admin_until": session.get("admin_until") if g.admin else None})


@bp.post("/api/admin-mode/unlock")
@login_required
def unlock_api():
    ok, code, msg = try_unlock(json_body().get("code", ""))
    if ok:
        return jsonify({"ok": True, "admin": True})
    return error_response(429 if code == "ADMIN_LOCKED" else 403, code, msg)


@bp.post("/api/admin-mode/lock")
@login_required
def lock_api():
    lock_admin()
    return jsonify({"ok": True, "admin": False})
