"""시연 상황 배치: 사용자A/B/C·테스트 계정으로 다양한 예약·좌석 상태를 만든다."""
from django.core.management import BaseCommand

from ... import clock
from ...services import setup_demo


class Command(BaseCommand):
    help = "시연 상황 배치 (관리자 화면의 [시연 상황 배치] 버튼과 같음)"

    def handle(self, *args, **opts):
        for line in setup_demo(clock.now()):
            self.stdout.write(line)
