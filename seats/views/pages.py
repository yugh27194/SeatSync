"""HTML 화면. 데이터는 각 화면의 JS가 API로 polling한다."""
from urllib.parse import urlparse

from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from .. import clock, qr
from ..auth import admin_required, login_required
from ..models import Seat
from ..services import log_admin


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
    """좌석 QR: 인쇄용 QR 파일(좌석별 PNG·A4 한 장 PDF)을 저장 폴더에 만들고 내려받는다.
    아래에는 인쇄 전에 화면에 띄운 QR을 폰으로 찍어 체크인·바로 예약을 시험하는 미리보기를 둔다.
    QR 주소 = {서비스 주소}/seat/{no}?t={좌석 토큰}."""
    import qrcode
    import qrcode.image.svg

    seats = list(Seat.objects.filter(active=True).order_by("no"))
    error = None
    if request.method == "POST":
        base = request.POST.get("base_url", "")
        try:
            m = qr.build(seats, base, clock.now())
        except ValueError as e:
            error = str(e)
        else:
            log_admin(request.user.id, "qr_build", clock.now(), memo=f"{m['base_url']} · {len(m['seats'])}석")
            return redirect("/admin/qr?built=1")

    manifest = qr.load_manifest()
    base_url = (manifest or {}).get("base_url") or qr.default_base_url(request)
    cards = []
    for seat in seats:
        url = qr.seat_url(seat, base_url)
        svg = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2).to_string(encoding="unicode")
        cards.append({"seat": seat, "url": url, "svg": svg})
    return render(request, "admin_qr.html", {
        "cards": cards, "manifest": manifest, "outdated": qr.outdated(manifest, seats), "error": error,
        "base_url": request.POST.get("base_url") if error else base_url, "built": request.GET.get("built") == "1",
        "qr_dir": str(qr.qr_dir()), "generated_at": (manifest or {}).get("generated_at", "")[:16].replace("T", " "),
        "local_host": urlparse(base_url).hostname in ("localhost", "127.0.0.1"),
    })


@admin_required
def admin_qr_file(request, name):
    """저장 폴더의 QR 파일 내려받기 (관리자만 — 좌석 토큰이 들어 있다)."""
    path = qr.stored_file(name)
    if path is None:
        raise Http404("파일이 없습니다. [QR 파일 새로 만들기]를 눌러 주세요.")
    resp = FileResponse(open(path, "rb"), as_attachment=request.GET.get("download") == "1", filename=name)
    resp["Cache-Control"] = "no-store"
    return resp


@ensure_csrf_cookie
@login_required
def history_page(request):
    return render(request, "history.html")


@ensure_csrf_cookie
@login_required
def congestion_page(request):
    return render(request, "congestion.html")
