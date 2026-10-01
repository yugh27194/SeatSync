"""좌석 QR 인쇄 파일 만들기: 좌석별 PNG + A4 한 장 PDF를 저장 폴더(기본 ./qr)에 저장한다.

    python manage.py make_qr --base-url https://jiyujin.pythonanywhere.com

관리자 화면 [좌석 QR] → [QR 파일 새로 만들기]와 같다. 좌석 토큰은 DB에 있으므로 서비스가 쓰는 DB에서 실행한다.
"""
from django.conf import settings
from django.core.management import BaseCommand, CommandError

from ... import clock
from ...models import Seat
from ...qr import build


class Command(BaseCommand):
    help = "좌석 QR 인쇄 파일(좌석별 PNG·A4 PDF) 만들기"

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default=settings.SEATSYNC.get("PUBLIC_URL") or "",
                            help="QR에 넣을 서비스 주소 (예: https://jiyujin.pythonanywhere.com)")
        parser.add_argument("--out", default=None, help="저장 폴더 (기본: SEATSYNC_QR_DIR 또는 ./qr)")

    def handle(self, *args, **opts):
        seats = list(Seat.objects.filter(active=True).order_by("no"))
        if not seats:
            raise CommandError("좌석이 없습니다. 먼저 `python manage.py init_db`를 실행하세요.")
        try:
            m = build(seats, opts["base_url"], clock.now(), out_dir=opts["out"])
        except ValueError as e:
            raise CommandError(f"{e} --base-url을 지정하세요.")
        out = opts["out"] or settings.SEATSYNC["QR_DIR"]
        for r in m["seats"]:
            self.stdout.write(f"{r['label']:>5}  {r['file']}  →  {r['url']}")
        self.stdout.write(f"A4 인쇄용: {out}/seats_A4.pdf ({m['pages']}장)")
        if not m["korean_font"]:
            self.stdout.write("한글 글꼴을 찾지 못해 카드 안내 문구를 영문으로 넣었습니다 (SEATSYNC_QR_FONT로 지정 가능).")
