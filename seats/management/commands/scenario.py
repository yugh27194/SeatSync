"""시연 시나리오 배치 (콘솔 전용 — 웹사이트에는 시연 상황을 만드는 기능이 없다).

    python manage.py scenario 1 --demo-settings
    python manage.py scenario --list
"""
from django.core.management import BaseCommand, CommandError

from ... import clock
from ...scenarios import SCENARIOS, apply_demo_settings, setup_scenario


class Command(BaseCommand):
    help = "시연 시나리오 배치: 1 · 1b · 2a · 2b · 3 · reset (docs/SCENARIOS.md)"

    def add_arguments(self, parser):
        parser.add_argument("name", nargs="?", help=" · ".join(SCENARIOS))
        parser.add_argument("--list", action="store_true", help="시나리오 목록")
        parser.add_argument("--demo-settings", action="store_true",
                            help="판정 기준을 시연용(1분 안팎)으로 바꾼다 (설정 화면 [시연 모드]와 같음)")

    def handle(self, *args, name=None, list=False, demo_settings=False, **opts):
        if list or not name:
            for k, v in SCENARIOS.items():
                self.stdout.write(f"{k:>5}  {v}")
            return
        if demo_settings:
            apply_demo_settings()
            self.stdout.write("판정 기준을 시연용으로 바꿨습니다 (장기 이석·무단 점유 1분, 판단 불가 30초).")
        try:
            for line in setup_scenario(name, clock.now()):
                self.stdout.write(self.style.SUCCESS(line))
        except ValueError as e:
            raise CommandError(str(e))
