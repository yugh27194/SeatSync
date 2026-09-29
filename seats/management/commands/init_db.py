"""DB 생성(migrate) + seed. --reset이면 DB 파일을 지우고 새로 만든다."""
import os

from django.conf import settings
from django.core.management import BaseCommand, call_command
from django.db import connections

from ...seed import seed


class Command(BaseCommand):
    help = "SeatSync DB 생성과 초기 데이터(좌석·설정·테스트 계정) 입력"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="DB 파일을 삭제하고 새로 만든다.")

    def handle(self, *args, reset=False, **opts):
        path = settings.DATABASES["default"]["NAME"]
        if reset and path != ":memory:":
            connections.close_all()
            for suffix in ("", "-wal", "-shm", "-journal"):
                if os.path.exists(str(path) + suffix):
                    os.remove(str(path) + suffix)
            self.stdout.write(f"삭제: {path}")
        call_command("migrate", verbosity=0)
        seed()
        self.stdout.write(self.style.SUCCESS(f"DB 초기화 완료: {path}"))
