"""외부 공유용 실행: waitress(운영용 WSGI 서버)로 실행한다. runserver는 개발용이라 외부 공개에 쓰지 않는다."""
from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command


class Command(BaseCommand):
    help = "SeatSync 서버 실행 (waitress). 예: python manage.py serve --port 5000"

    def add_arguments(self, parser):
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", type=int, default=5000)
        parser.add_argument("--threads", type=int, default=8)

    def handle(self, *args, host, port, threads, **opts):
        try:
            from waitress import serve
        except ImportError:
            raise CommandError("waitress가 없습니다. `pip install -r requirements.txt` 를 실행하세요.")
        call_command("migrate", verbosity=0)  # 업데이트 후 새 표가 있으면 만든다
        code = settings.SEATSYNC["ADMIN_CODE"]
        self.stdout.write(self.style.SUCCESS(f"SeatSync 실행 중 → http://localhost:{port}  (끄려면 Ctrl+C)"))
        if code in ("admin", "0000") or len(code) < 6:
            self.stdout.write(self.style.WARNING(
                f"[주의] 관리자 코드가 '{code}'입니다. 외부 링크로 공개한다면 SEATSYNC_ADMIN_CODE 를 추측하기 어려운 값으로 바꾸세요."))
        if settings.SECRET_KEY == "dev-secret-change-me":
            self.stdout.write(self.style.WARNING("[주의] SEATSYNC_SECRET_KEY 가 기본값입니다. 외부 공개 시 바꾸세요."))
        serve(__import__("seatsync.wsgi", fromlist=["application"]).application,
              host=host, port=port, threads=threads, ident="SeatSync")
