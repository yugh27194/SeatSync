"""HTML 화면. 데이터는 각 화면의 JS가 API로 polling한다."""
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

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


@require_GET
@ensure_csrf_cookie
@admin_required
def admin_qr_page(request):
    """좌석 QR 보관함: 이미 출력해 좌석에 붙인 QR을 A4 한 장으로 보고 다시 인쇄한다.
    QR은 새로 만들거나 바꾸지 않는다 — 좌석에 저장된 토큰으로 처음 출력할 때와 똑같은 주소
    ({접속 주소}/seat/{no}?t={좌석 토큰})를 같은 방식(SVG, box_size 10, border 2)으로 그릴 뿐이다."""
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


@ensure_csrf_cookie
@admin_required
def admin_congestion_page(request):
    """관리자 탭 [이용 분석]: 혼잡도 + 실사용률·유휴 점유·처리 필요 비율."""
    return render(request, "congestion.html", {"admin_view": True})
