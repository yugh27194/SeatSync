# PythonAnywhere WSGI 설정 (deploy/pythonanywhere_deploy.py가 /var/www/<도메인>_wsgi.py 로 복사한다).
# 비밀 값(SECRET_KEY, 관리자 코드, 디바이스 키)은 코드가 아니라 ~/.seatsync.env 에서 읽는다.
import os
import sys

PROJECT = os.path.expanduser("~/SeatSync")
ENV_FILE = os.path.expanduser("~/.seatsync.env")

if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

if os.path.exists(ENV_FILE):
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "seatsync.settings")
os.environ.setdefault("SEATSYNC_HTTPS", "1")
os.environ.setdefault("SEATSYNC_DB", os.path.join(PROJECT, "seatsync.db"))

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
