# PythonAnywhere의 Web 탭 → WSGI configuration file 내용을 이 파일로 바꾼다.
# USERNAME과 아래 값들을 본인 것으로 수정한 뒤 [Reload]를 누른다.
import os
import sys

PROJECT = "/home/USERNAME/SeatSync"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

os.environ["DJANGO_SETTINGS_MODULE"] = "seatsync.settings"
os.environ["SEATSYNC_HTTPS"] = "1"                      # HTTPS 전용 배포
os.environ["SEATSYNC_SECRET_KEY"] = "여기에-긴-무작위-문자열"  # python -c "import secrets;print(secrets.token_urlsafe(50))"
os.environ["SEATSYNC_ADMIN_CODE"] = "admin"             # 관리자 모드 코드 (공개 사이트라면 추측하기 어려운 값 권장)
os.environ["SEATSYNC_DEVICE_KEY"] = "dev-key"           # Pi가 /api/detections 에 보낼 키
os.environ["SEATSYNC_DB"] = PROJECT + "/seatsync.db"

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
