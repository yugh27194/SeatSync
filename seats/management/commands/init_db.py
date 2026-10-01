"""DB 생성(migrate) + seed. --reset이면 DB 파일을 지우고 새로 만든다.

좌석 QR 토큰은 이미 출력해 붙여 둔 QR과 연결되어 있으므로 --reset에서도 유지한다(--new-qr일 때만 새로 만든다).
"""
import os
import sqlite3

from django.conf import settings
from django.core.management import BaseCommand, call_command
from django.db import connections

from ...models import Seat
from ...seed import seed


def saved_tokens(path):
    """기존 DB 파일의 좌석 QR 토큰 {좌석 번호: 토큰}. 읽을 수 없으면 {}."""
    if not os.path.exists(str(path)):
        return {}
    try:
        with sqlite3.connect(str(path)) as conn:
            return dict(conn.execute("SELECT no, qr_token FROM seats_seat WHERE qr_token <> ''").fetchall())
    except sqlite3.Error:
        return {}


class Command(BaseCommand):
    help = "SeatSync DB 생성과 초기 데이터(좌석·설정·테스트 계정) 입력"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="DB 파일을 삭제하고 새로 만든다(좌석 QR 토큰은 유지).")
        parser.add_argument("--new-qr", action="store_true",
                            help="--reset 때 좌석 QR 토큰도 새로 만든다 (출력해 둔 QR은 더 이상 쓸 수 없음).")

    def handle(self, *args, reset=False, new_qr=False, **opts):
        path = settings.DATABASES["default"]["NAME"]
        tokens = {}
        if reset and path != ":memory:":
            tokens = {} if new_qr else saved_tokens(path)
            connections.close_all()
            for suffix in ("", "-wal", "-shm", "-journal"):
                if os.path.exists(str(path) + suffix):
                    os.remove(str(path) + suffix)
            self.stdout.write(f"삭제: {path}")
        call_command("migrate", verbosity=0)
        seed()
        for no, token in tokens.items():
            Seat.objects.filter(no=no).update(qr_token=token)
        if tokens:
            self.stdout.write(f"좌석 QR 토큰 {len(tokens)}개 유지 (출력해 둔 QR 그대로 사용)")
        self.stdout.write(self.style.SUCCESS(f"DB 초기화 완료: {path}"))
