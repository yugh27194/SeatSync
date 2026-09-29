"""로그인/로그아웃/회원가입, login_required, admin_required."""
import sqlite3
from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db
from routes import error_response, now_ts

bp = Blueprint("auth", __name__)


def _is_api():
    return request.path.startswith("/api/")


def load_user():
    g.user = None
    uid = session.get("uid")
    if uid is not None:
        row = get_db().execute("SELECT id, student_no, name, role FROM users WHERE id = ?", (uid,)).fetchone()
        if row:
            g.user = dict(row)
        else:
            session.clear()


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
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            if _is_api():
                return error_response(403, "FORBIDDEN", "관리자만 사용할 수 있습니다.")
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _safe_next(target):
    """오픈 리다이렉트 방지: 같은 사이트 내부 경로만 허용."""
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def _home_for(user, next_url=None):
    if user["role"] == "admin" and not next_url:
        return url_for("pages.admin")
    return next_url or url_for("pages.map_page")


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
            # 관리자는 /admin으로. 단 QR 페이지 등 next가 있으면 그쪽으로 복귀.
            return redirect(_home_for(row, next_url))
        error = "학번 또는 비밀번호가 올바르지 않습니다."
    elif g.user:
        return redirect(_home_for(g.user, next_url))
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
    session.clear()
    return redirect(url_for("auth.login"))
