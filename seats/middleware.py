"""요청마다 관리자 권한 여부 계산, API 에러 변환, DB 준비 여부 확인."""
from django.conf import settings
from django.db import connection
from django.http import HttpResponse

from .auth import is_admin
from .http import ApiError, error_response

RESET_HINT = "DB가 준비되지 않았습니다. `python manage.py init_db` (구조가 바뀌었다면 `python manage.py init_db --reset`) 을 실행하세요."


class SeatSyncMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._ready = False

    def __call__(self, request):
        if not self._ready and not request.path.startswith("/static/"):
            if "seats_seat" not in connection.introspection.table_names():
                if request.path.startswith("/api/"):
                    return error_response(500, "DB_RESET_REQUIRED", RESET_HINT)
                return HttpResponse(f"<h1>SeatSync</h1><p>{RESET_HINT}</p>", status=500)
            self._ready = True
        request.admin = is_admin(request) if hasattr(request, "user") else False
        return self.get_response(request)

    def process_exception(self, request, exc):
        if isinstance(exc, ApiError):
            return error_response(exc.status, exc.code, exc.message)
        return None


def _static_version():
    """정적 파일(JS·CSS)이 바뀌면 달라지는 값. 주소 뒤에 ?v= 로 붙여 배포 직후 브라우저 캐시 대신 새 파일을 받게 한다."""
    root = settings.BASE_DIR / "static"
    try:
        return str(int(max(f.stat().st_mtime for f in root.rglob("*") if f.is_file())))
    except ValueError:
        return "0"


STATIC_VERSION = _static_version()


def context(request):
    """템플릿 공통 변수."""
    user = getattr(request, "user", None)
    return {
        "current_user": user if user is not None and user.is_authenticated else None,
        "admin_mode": getattr(request, "admin", False),
        "static_ver": STATIC_VERSION,
    }
