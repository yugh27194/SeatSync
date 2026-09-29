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
@admin_required
def admin_qr_page(request):
    """좌석 QR 체크인 (임시): 인쇄 전에도 화면에 띄운 QR을 폰으로 찍어 체크인·바로 예약을 시험할 수 있게 한다.
    QR 주소 = 지금 접속한 주소 기준 /seat/{no}?t={좌석 토큰}."""
    import qrcode
    import qrcode.image.svg

    cards = []
    for seat in Seat.objects.filter(active=True).order_by("no"):
        url = request.build_absolute_uri(f"/seat/{seat.no}?t={seat.qr_token}")
        svg = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2).to_string(encoding="unicode")
        cards.append({"seat": seat, "url": url, "svg": svg})
    return render(request, "admin_qr.html", {"cards": cards})


@ensure_csrf_cookie
@login_required
def history_page(request):
    return render(request, "history.html")


@ensure_csrf_cookie
@login_required
def congestion_page(request):
    return render(request, "congestion.html")
