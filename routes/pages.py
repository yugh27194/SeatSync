"""HTML 화면 라우트. 데이터는 각 화면의 JS가 API로 polling한다."""
from flask import Blueprint, g, redirect, render_template, request, url_for

from auth import admin_required, login_required
from db import get_db

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    if g.user is None:
        return redirect(url_for("auth.login"))
    if g.user["role"] == "admin":
        return redirect(url_for("pages.admin"))
    return redirect(url_for("pages.map_page"))


@bp.route("/map")
@login_required
def map_page():
    return render_template("map.html")


@bp.route("/seat/<int:no>")
@login_required
def seat_page(no):
    seat = get_db().execute("SELECT no, label, zone FROM seats WHERE no = ? AND active = 1", (no,)).fetchone()
    if seat is None:
        return render_template("seat.html", seat=None, token=""), 404
    return render_template("seat.html", seat=dict(seat), token=request.args.get("t", ""))


@bp.route("/my")
@login_required
def my():
    return render_template("my.html")


@bp.route("/admin")
@admin_required
def admin():
    return render_template("admin.html")


@bp.route("/admin/settings")
@admin_required
def admin_settings():
    return render_template("admin_settings.html")
