"""SeatSync Django 설정. 값은 환경변수로 바꾼다 (README 참고)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SEATSYNC_SECRET_KEY", "dev-secret-change-me")
DEBUG = os.environ.get("SEATSYNC_DEBUG", "0") == "1"
# 노트북에서 실행하고 같은 Wi-Fi의 폰·Pi가 IP로 접속하므로 호스트를 제한하지 않는다(로컬 네트워크 전제).
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "seats",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "seats.middleware.SeatSyncMiddleware",
]

ROOT_URLCONF = "seatsync.urls"
WSGI_APPLICATION = "seatsync.wsgi.application"
APPEND_SLASH = False

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": False,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "seats.middleware.context",
    ]},
}]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SEATSYNC_DB", str(BASE_DIR / "seatsync.db")),
        # 쓰기 트랜잭션을 BEGIN IMMEDIATE로 직렬화 (동시 예약 경합 방지)
        "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 15},
    }
}

AUTH_USER_MODEL = "seats.User"
LOGIN_URL = "/login"
SESSION_COOKIE_AGE = 14 * 24 * 3600

# DB에는 UTC epoch 초를 저장하고, 표시는 SEATSYNC_TZ(기본 KST)로 한다.
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "UTC"
USE_TZ = True
USE_I18N = False

STATIC_URL = "/static/"
STATIC_DIR = BASE_DIR / "static"
STATICFILES_DIRS = [STATIC_DIR]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- SeatSync
SEATSYNC = {
    "DEVICE_KEY": os.environ.get("SEATSYNC_DEVICE_KEY", "dev-key"),
    "TZ": os.environ.get("SEATSYNC_TZ", "Asia/Seoul"),
    # 관리자 계정 대신, 관리자 모드를 켤 때 이 코드를 입력한다.
    "ADMIN_CODE": os.environ.get("SEATSYNC_ADMIN_CODE", "0000"),
    "ADMIN_MODE_MIN": int(os.environ.get("SEATSYNC_ADMIN_MODE_MIN", "60")),
    "SEATS_FILE": BASE_DIR / "config" / "seats.json",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR"}},
}
