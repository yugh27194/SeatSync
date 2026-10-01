"""DB 생성(migrate) + seed. --reset이면 DB 파일을 지우고 새로 만든다.

- 좌석 QR 토큰은 이미 출력해 붙여 둔 QR과 연결되어 있으므로 어떤 경우에도 바꾸지 않는다(--reset에서도 복원).
- --sample: 시연용 샘플(지난 4주 이용 기록 + 시연 상황 배치)을 서버를 처음 만들 때 한 번만 만든다.
  웹사이트에서는 만들 수 없다(실제 기록과 섞이지 않게). 이미 만든 적이 있으면 건너뛴다.
"""
import os
import sqlite3

from django.conf import settings
from django.core.management import BaseCommand, call_command
from django.db import connections

from ... import clock
from ...models import AdminLog, Seat
from ...seed import seed

SAMPLE_MARK = "sample_init"  # 샘플을 만든 적이 있는지 표시하는 처리 이력


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
        parser.add_argument("--sample", action="store_true",
                            help="시연용 샘플(지난 4주 이용 기록 + 시연 상황)을 처음 한 번만 만든다.")

    def handle(self, *args, reset=False, sample=False, **opts):
        path = settings.DATABASES["default"]["NAME"]
        tokens = {}
        if reset and path != ":memory:":
            tokens = saved_tokens(path)
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
        if sample:
            self.make_sample()
        self.stdout.write(self.style.SUCCESS(f"DB 초기화 완료: {path}"))

    def make_sample(self):
        if AdminLog.objects.filter(action=SAMPLE_MARK).exists():
            self.stdout.write("시연용 샘플은 이미 만들어 두었으므로 건너뜁니다.")
            return
        from ...sample import generate_history
        from ...services import log_admin, setup_demo
        now = clock.now()
        n = generate_history(now, weeks=4)
        msgs = setup_demo(now)
        log_admin(None, SAMPLE_MARK, now, memo=f"지난 4주 예약 {n}건 · 시연 상황 {len(msgs)}석")
        self.stdout.write(f"시연용 샘플 생성: 지난 4주 예약 {n}건")
        for line in msgs:
            self.stdout.write("  " + line)
