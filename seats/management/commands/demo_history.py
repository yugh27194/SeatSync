"""샘플 이력 생성: 지난 몇 주의 이용 기록·상태 전이를 만들어 '내 이용 기록'과 '혼잡도'를 시연한다."""
from django.core.management import BaseCommand

from ... import clock
from ...sample import generate_history


class Command(BaseCommand):
    help = "지난 N주 샘플 이력 생성 (관리자 화면의 [샘플 이력 생성] 버튼과 같음)"

    def add_arguments(self, parser):
        parser.add_argument("--weeks", type=int, default=4)

    def handle(self, *args, weeks=4, **opts):
        n = generate_history(clock.now(), weeks=weeks)
        self.stdout.write(self.style.SUCCESS(f"샘플 이력 생성 완료: 지난 {weeks}주 · 예약 {n}건"))
