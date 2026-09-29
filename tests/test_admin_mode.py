"""관리자 계정 없이, 관리자 모드를 켜고 끄는 방식."""
from conftest import ADMIN_CODE, ApiClient, err, login

from seats import auth
from seats.models import User


def test_no_admin_account_seeded():
    assert not User.objects.filter(student_no="admin").exists()


def test_api_requires_admin_mode(user):
    r = user.get("/api/admin/seats")
    assert r.status_code == 403 and err(r) == "ADMIN_REQUIRED" and r.json()["error"]["message"] == "관리자 권한이 필요합니다."
    assert user.jget("/api/admin-mode")["admin"] is False


def test_page_shows_code_prompt(user):
    r = user.get("/admin")
    html = r.content.decode()
    assert r.status_code == 403 and "관리자 권한이 필요합니다." in html and 'name="code"' in html


def test_header_switch_rendered(user, admin):
    assert 'id="admin-toggle"' in user.get("/map").content.decode()
    assert 'aria-checked="false"' in user.get("/map").content.decode()
    html = admin.get("/map").content.decode()
    assert 'aria-checked="true"' in html and 'data-admin="1"' in html


def test_turn_on_and_off(user):
    r = user.jpost("/api/admin-mode/unlock", {"code": "wrong"})
    assert r.status_code == 403 and err(r) == "BAD_ADMIN_CODE" and "4회" in r.json()["error"]["message"]
    assert user.jpost("/api/admin-mode/unlock", {"code": ADMIN_CODE}).status_code == 200
    assert user.get("/api/admin/seats").status_code == 200
    assert "attention" in user.jget("/api/seats")["seats"][0]
    assert user.jpost("/api/admin-mode/lock").json()["admin"] is False     # 스위치 끄기
    assert user.get("/api/admin/seats").status_code == 403
    assert "attention" not in user.jget("/api/seats")["seats"][0]
    actions = [a for a in ("admin_on", "admin_off")]
    user.jpost("/api/admin-mode/unlock", {"code": ADMIN_CODE})
    logged = [x["action"] for x in user.jget("/api/admin/log")["log"]]
    assert logged[:3] == ["admin_on", "admin_off", "admin_on"] and actions


def test_unlock_via_page_redirects(user):
    r = user.post("/admin/unlock", {"code": ADMIN_CODE, "next": "/admin/settings"})
    assert r.status_code == 302 and r["Location"] == "/admin/settings"
    assert user.get("/admin").status_code == 200
    r = user.post("/admin/unlock", {"code": ADMIN_CODE, "next": "https://evil.example"})
    assert r["Location"] == "/admin"


def test_lockout_after_five_failures(user, clock):
    for _ in range(4):
        user.jpost("/api/admin-mode/unlock", {"code": "x"})
    r = user.jpost("/api/admin-mode/unlock", {"code": "x"})
    assert r.status_code == 429 and err(r) == "ADMIN_LOCKED"
    assert user.jpost("/api/admin-mode/unlock", {"code": ADMIN_CODE}).status_code == 429
    clock.advance(auth.LOCK_SEC)
    assert user.jpost("/api/admin-mode/unlock", {"code": ADMIN_CODE}).status_code == 200


def test_lockout_survives_new_session():
    c = login(ApiClient())
    for _ in range(5):
        c.jpost("/api/admin-mode/unlock", {"code": "x"})
    c2 = login(ApiClient())
    assert c2.jpost("/api/admin-mode/unlock", {"code": ADMIN_CODE}).status_code == 429


def test_admin_mode_is_per_session(admin):
    other = login(ApiClient(), "20260005")
    assert other.get("/api/admin/seats").status_code == 403
    assert admin.get("/api/admin/seats").status_code == 200


def test_expires_after_idle(admin, clock, settings):
    mins = settings.SEATSYNC["ADMIN_MODE_MIN"]
    clock.advance(mins * 60 - 10)
    assert admin.get("/api/admin/seats").status_code == 200   # 사용하면 연장
    clock.advance(mins * 60 - 10)
    assert admin.get("/api/admin/seats").status_code == 200
    clock.advance(mins * 60 + 1)
    assert err(admin.get("/api/admin/seats")) == "ADMIN_REQUIRED"


def test_logout_turns_off(admin):
    admin.get("/logout")
    login(admin, "20260005")
    assert admin.get("/api/admin/seats").status_code == 403


def test_login_redirects_to_map():
    r = ApiClient().post("/login", {"student_no": "userA", "password": "1234"})
    assert r["Location"] == "/map"


def test_login_required_redirect_keeps_qr_url():
    r = ApiClient().get("/seat/3?t=abc")
    assert r.status_code == 302 and r["Location"] == "/login?next=/seat/3%3Ft%3Dabc"


def test_default_admin_code_is_admin():
    from seatsync import settings as project_settings
    import os
    if "SEATSYNC_ADMIN_CODE" not in os.environ:
        assert project_settings.SEATSYNC["ADMIN_CODE"] == "admin"


def _csrf_client_login(origin, host):
    """실제 브라우저처럼 CSRF 검사를 켠 클라이언트로 외부 주소(터널)에서 로그인·API 호출."""
    from django.test import Client
    c = Client(enforce_csrf_checks=True, HTTP_HOST=host)
    c.get("/login")
    token = c.cookies["csrftoken"].value
    r = c.post("/login", {"student_no": "userA", "password": "1234", "csrfmiddlewaretoken": token}, HTTP_ORIGIN=origin)
    return c, r


def test_tunnel_origin_passes_csrf():
    c, r = _csrf_client_login("https://quiet-river-1234.trycloudflare.com", "quiet-river-1234.trycloudflare.com")
    assert r.status_code == 302 and r["Location"] == "/map"
    token = c.cookies["csrftoken"].value
    r = c.post("/api/admin-mode/unlock", data='{"code": "test-code"}', content_type="application/json",
               HTTP_ORIGIN="https://quiet-river-1234.trycloudflare.com", HTTP_X_CSRFTOKEN=token)
    assert r.status_code == 200


def test_foreign_origin_blocked():
    c, r = _csrf_client_login("https://evil.example", "quiet-river-1234.trycloudflare.com")
    assert r.status_code == 403


def test_extra_trusted_origin(settings):
    settings.CSRF_TRUSTED_ORIGINS = settings.CSRF_TRUSTED_ORIGINS + ["https://seat.example.com"]
    _c, r = _csrf_client_login("https://seat.example.com", "seat.example.com")
    assert r.status_code == 302


def test_admin_qr_page(admin, user):
    from conftest import qr_token
    html = admin.get("/admin/qr").content.decode()
    assert html.count("<svg") == 20 and f"/seat/1?t={qr_token(1)}" in html
    r = user.get("/admin/qr")
    assert r.status_code != 200 or "<svg" not in r.content.decode()   # 관리자 모드가 아니면 QR(토큰)을 보여 주지 않는다
