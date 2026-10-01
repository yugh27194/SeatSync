"""로그인·회원가입·로그아웃, 관리자 탭 권한(관리자 코드 확인)."""
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from .. import clock
from ..auth import login_required, lock_info, safe_next, try_unlock
from ..http import error_response, jres, json_body
from ..models import User


def login_view(request):
    next_url = safe_next(request.POST.get("next") or request.GET.get("next"))
    error = None
    if request.method == "POST":
        user = authenticate(request, username=(request.POST.get("student_no") or "").strip(),
                            password=request.POST.get("password") or "")
        if user is not None:
            login(request, user)
            return redirect(next_url or "/map")
        error = "학번 또는 비밀번호가 맞지 않아요."
    elif request.user.is_authenticated:
        return redirect(next_url or "/map")
    return render(request, "login.html", {"error": error, "next_url": next_url or ""})


def signup_view(request):
    next_url = safe_next(request.POST.get("next") or request.GET.get("next"))
    error = None
    form = {"student_no": "", "name": ""}
    if request.method == "POST":
        form["student_no"] = student_no = (request.POST.get("student_no") or "").strip()
        form["name"] = name = (request.POST.get("name") or "").strip()
        password = request.POST.get("password") or ""
        if not student_no or not name:
            error = "학번과 이름을 입력해 주세요."
        elif len(password) < 4:
            error = "비밀번호는 4자 이상으로 정해 주세요."
        elif User.objects.filter(student_no=student_no).exists():
            error = "이미 가입된 학번이에요."
        else:
            try:
                user = User.objects.create(student_no=student_no, name=name, password=make_password(password),
                                           created_at=clock.now())
            except IntegrityError:
                error = "이미 가입된 학번이에요."
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                return redirect(next_url or "/map")
    return render(request, "signup.html", {"error": error, "form": form, "next_url": next_url or ""})


def logout_view(request):
    logout(request)  # 세션을 비우므로 관리자 탭 권한도 함께 사라진다
    return redirect("/login")


# ---------------------------------------------------------------- 관리자 탭 권한 (관리자 코드)

@require_POST
@login_required
def admin_unlock_page(request):
    next_url = safe_next(request.POST.get("next")) or "/admin"
    ok, _code, msg = try_unlock(request, request.POST.get("code", ""))
    if ok:
        return redirect(next_url)
    return render(request, "admin_unlock.html", {"next_url": next_url, "error": msg, **lock_info(request)}, status=403)


@require_GET
@login_required
def admin_mode_status(request):
    return jres({"admin": request.admin})


@require_POST
@login_required
def admin_mode_unlock(request):
    ok, code, msg = try_unlock(request, json_body(request).get("code", ""))
    if ok:
        return jres({"ok": True, "admin": True})
    return error_response(429 if code == "ADMIN_LOCKED" else 403, code, msg)
