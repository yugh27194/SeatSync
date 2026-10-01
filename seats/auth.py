"""로그인 데코레이터와 관리자 탭 권한.

관리자 계정은 따로 두지 않는다. 관리자 개입이 필요한 기능은 모두 [관리자] 탭(/admin…)에만 있다.
로그인한 사용자가 [관리자] 탭을 처음 누르면 관리자 코드를 묻고, 맞으면 그 세션 동안(로그아웃 전까지)
다시 묻지 않는다. 다른 탭에 갔다가 돌아와도 그대로 들어간다.
"""
import hmac
import threading
from functools import wraps
from urllib.parse import quote, urlparse

from django.conf import settings
from django.shortcuts import redirect, render

from . import clock
from .http import error_response
from .services import log_admin

ADMIN_REQUIRED_MSG = "관리자 코드가 필요해요."
MAX_FAILS = 5         # 연속 실패 허용 횟수
LOCK_SEC = 5 * 60     # 초과 시 잠금 시간
SESSION_KEY = "admin_ok"  # 관리자 코드를 통과한 세션

# 사용자별 관리자 코드 실패 기록 {user_id: [실패 횟수, 잠금 해제 시각]}.
# 세션 쿠키를 지워도 우회되지 않게 서버 메모리에 둔다(단일 프로세스 가정).
_fails = {}
_fails_lock = threading.Lock()


def _is_api(request):
    return request.path.startswith("/api/")


def is_admin(request):
    return request.user.is_authenticated and request.session.get(SESSION_KEY) is True


def safe_next(target):
    """오픈 리다이렉트 방지: 같은 사이트 내부 경로만 허용."""
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def login_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if _is_api(request):
                return error_response(401, "UNAUTHENTICATED", "로그인해 주세요.")
            return redirect("/login?next=" + quote(request.get_full_path(), safe="/"))
        return view(request, *args, **kwargs)
    return wrapped


def admin_required(view):
    """관리자 코드를 통과한 세션이 아니면 API는 403 ADMIN_REQUIRED, 화면은 관리자 코드 입력 화면."""
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.admin:
            if _is_api(request):
                return error_response(403, "ADMIN_REQUIRED", ADMIN_REQUIRED_MSG)
            ctx = {"next_url": request.get_full_path(), "error": None, **lock_info(request)}
            return render(request, "admin_unlock.html", ctx, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def lock_info(request):
    with _fails_lock:
        cnt, until = _fails.get(request.user.id, [0, 0])
    now = clock.now()
    return {"locked_sec": max(0, until - now), "remaining": max(0, MAX_FAILS - cnt) if until <= now else 0}


def try_unlock(request, code):
    """관리자 코드 확인 → 이 세션에서 관리자 탭 허용. (성공 여부, 에러 코드, 메시지)"""
    uid, now = request.user.id, clock.now()
    with _fails_lock:
        cnt, until = _fails.get(uid, [0, 0])
        if until > now:
            mins = (until - now + 59) // 60
            return False, "ADMIN_LOCKED", f"잠시 잠겼어요. {mins}분 뒤 다시 시도해 주세요."
        if until:  # 잠금이 끝났으면 초기화
            cnt = 0
        expected = settings.SEATSYNC["ADMIN_CODE"]
        ok = isinstance(code, str) and bool(code) and hmac.compare_digest(code.encode(), expected.encode())
        if ok:
            _fails.pop(uid, None)
        else:
            cnt += 1
            _fails[uid] = [cnt, now + LOCK_SEC if cnt >= MAX_FAILS else 0]
    if ok:
        request.session[SESSION_KEY] = True  # 로그아웃(세션 종료) 전까지 유지
        request.admin = True
        log_admin(uid, "admin_on", now)
        return True, None, None
    if cnt >= MAX_FAILS:
        log_admin(uid, "admin_locked", now, memo=f"관리자 코드 {MAX_FAILS}회 실패")
        return False, "ADMIN_LOCKED", f"{MAX_FAILS}회 틀려서 잠겼어요. {LOCK_SEC // 60}분 뒤 다시 시도해 주세요."
    return False, "BAD_ADMIN_CODE", f"관리자 코드가 맞지 않아요. (남은 시도 {MAX_FAILS - cnt}회)"
