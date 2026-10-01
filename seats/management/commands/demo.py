"""시연 상황 배치: 사용자A/B/C·테스트 계정으로 다양한 예약·좌석 상태를 만든다."""
from django.core.management import BaseCommand

from ... import clock
from ...services import setup_demo


class Command(BaseCommand):
    help = "시연 상황 배치 (콘솔 전용 — 서버를 처음 만들 때는 init_db --sample)"

    def handle(self, *args, **opts):
        for line in setup_demo(clock.now()):
            self.stdout.write(line)
