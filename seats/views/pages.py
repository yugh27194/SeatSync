"""HTML 화면. 데이터는 각 화면의 JS가 API로 polling한다."""
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from ..auth import admin_required, login_required
from ..models import Seat


def index(request):
    return redirect("/map" if request.user.is_authenticated else "/login")


@ensure_csrf_cookie
@login_required
def map_page(request):
    return render(request, "map.html")


@ensure_csrf_cookie
@login_required
def seat_page(request, no):
    seat = Seat.objects.filter(no=no, active=True).first()
    if seat is None:
        return render(request, "seat.html", {"seat": None, "token": ""}, status=404)
    return render(request, "seat.html", {"seat": seat, "token": request.GET.get("t", "")})


@ensure_csrf_cookie
@login_required
def my_page(request):
    return render(request, "my.html")


@ensure_csrf_cookie
@admin_required
def admin_page(request):
    return render(request, "admin.html")


@ensure_csrf_cookie
@admin_required
def admin_settings_page(request):
    return render(request, "admin_settings.html")


@ensure_csrf_cookie
@login_required
def history_page(request):
    return render(request, "history.html")


@ensure_csrf_cookie
@login_required
def congestion_page(request):
    return render(request, "congestion.html")
