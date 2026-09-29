"""관리자 계정 없이, 관리자 코드로 권한을 해금하는 방식."""
from conftest import ADMIN_CODE, login

import auth


def err(r):
    return r.get_json()["error"]


def test_no_admin_account_seeded(conn):
    assert conn.execute("SELECT COUNT(*) FROM users WHERE student_no='admin'").fetchone()[0] == 0


def test_api_requires_admin_mode(user):
    r = user.get("/api/admin/seats")
    assert r.status_code == 403
    assert err(r)["code"] == "ADMIN_REQUIRED" and err(r)["message"] == "관리자 권한이 필요합니다."
    assert user.get("/api/admin-mode").get_json()["admin"] is False


def test_page_shows_code_prompt(user):
    r = user.get("/admin")
    assert r.status_code == 403
    html = r.get_data(as_text=True)
    assert "관리자 권한이 필요합니다." in html and 'name="code"' in html


def test_unlock_via_api(user):
    r = user.post("/api/admin-mode/unlock", json={"code": "wrong"})
    assert r.status_code == 403 and err(r)["code"] == "BAD_ADMIN_CODE" and "4회" in err(r)["message"]
    assert user.post("/api/admin-mode/unlock", json={"code": ADMIN_CODE}).status_code == 200
    assert user.get("/api/admin/seats").status_code == 200
    log = user.get("/api/admin/log").get_json()["log"]
    assert log[0]["action"] == "admin_on" and log[0]["admin_name"] == "테스트1"


def test_unlock_via_page_redirects(user):
    r = user.post("/admin/unlock", data={"code": ADMIN_CODE, "next": "/admin/settings"})
    assert r.status_code == 302 and r.headers["Location"] == "/admin/settings"
    assert user.get("/admin").status_code == 200
    r = user.post("/admin/unlock", data={"code": ADMIN_CODE, "next": "https://evil.example"})
    assert r.headers["Location"] == "/admin"


def test_lockout_after_five_failures(user, clock):
    for _ in range(4):
        user.post("/api/admin-mode/unlock", json={"code": "x"})
    r = user.post("/api/admin-mode/unlock", json={"code": "x"})
    assert r.status_code == 429 and err(r)["code"] == "ADMIN_LOCKED"
    # 잠금 중에는 맞는 코드도 거절
    assert user.post("/api/admin-mode/unlock", json={"code": ADMIN_CODE}).status_code == 429
    clock.advance(auth.LOCK_SEC)
    assert user.post("/api/admin-mode/unlock", json={"code": ADMIN_CODE}).status_code == 200


def test_lockout_survives_cookie_reset(app):
    c = login(app.test_client())
    for _ in range(5):
        c.post("/api/admin-mode/unlock", json={"code": "x"})
    c2 = login(app.test_client())  # 새 세션(쿠키 초기화)
    assert c2.post("/api/admin-mode/unlock", json={"code": ADMIN_CODE}).status_code == 429


def test_admin_mode_is_per_session(app, admin):
    other = login(app.test_client(), "20260005")  # 같은 사용자라도 다른 세션은 잠겨 있음
    assert other.get("/api/admin/seats").status_code == 403
    assert admin.get("/api/admin/seats").status_code == 200


def test_expires_after_idle(admin, clock, app):
    clock.advance(app.config["ADMIN_MODE_MIN"] * 60 - 10)
    assert admin.get("/api/admin/seats").status_code == 200   # 사용하면 연장
    clock.advance(app.config["ADMIN_MODE_MIN"] * 60 - 10)
    assert admin.get("/api/admin/seats").status_code == 200
    clock.advance(app.config["ADMIN_MODE_MIN"] * 60 + 1)
    assert err(admin.get("/api/admin/seats"))["code"] == "ADMIN_REQUIRED"


def test_lock_and_logout(admin, app):
    assert admin.post("/api/admin-mode/lock").get_json()["admin"] is False
    assert admin.get("/api/admin/seats").status_code == 403
    admin.post("/api/admin-mode/unlock", json={"code": ADMIN_CODE})
    admin.get("/logout")
    login(admin, "20260005")
    assert admin.get("/api/admin/seats").status_code == 403


def test_login_redirects_to_map(app):
    r = app.test_client().post("/login", data={"student_no": "userA", "password": "1234"})
    assert r.headers["Location"] == "/map"


def test_old_schema_shows_reset_hint(app, conn):
    conn.execute("PRAGMA user_version = 1")
    r = app.test_client().get("/api/seats")
    assert r.status_code == 500 and err(r)["code"] == "DB_RESET_REQUIRED"
