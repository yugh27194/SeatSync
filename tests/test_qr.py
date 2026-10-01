"""좌석 QR 보관함: 이미 출력한 QR을 그대로 보여 주고(새로 만들거나 바꾸지 않음) A4로 인쇄, QR → 로그인 → 체크인."""
from conftest import ApiClient, qr_token

from seats.models import Reservation, Seat


def test_qr_page_shows_existing_qr(admin):
    """예전 /admin/qr에서 출력한 QR과 같은 주소({접속 주소}/seat/{no}?t={토큰})를 같은 방식으로 그린다."""
    import qrcode
    import qrcode.image.svg
    page = admin.get("/admin/qr").content.decode()
    assert "좌석 QR 보관함" in page and "A4로 인쇄" in page and 'class="a4"' in page
    for seat in Seat.objects.filter(active=True):
        url = f"http://testserver/seat/{seat.no}?t={seat.qr_token}"
        assert url in page
    seat = Seat.objects.get(no=1)
    svg = qrcode.make(f"http://testserver/seat/1?t={seat.qr_token}", image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=10, border=2).to_string(encoding="unicode")
    assert svg in page  # 처음 출력할 때와 같은 QR 그림
    assert page.count('class="a4-card"') == Seat.objects.filter(active=True).count()


def test_qr_page_never_changes_tokens(admin):
    before = dict(Seat.objects.values_list("no", "qr_token"))
    for _ in range(2):
        admin.get("/admin/qr")
    assert admin.post("/admin/qr").status_code == 405  # 만들기·바꾸기 기능 없음
    from seats.seed import seed
    seed()
    assert dict(Seat.objects.values_list("no", "qr_token")) == before


def test_qr_page_admin_only(user):
    r = user.get("/admin/qr")
    assert r.status_code == 403 and "관리자 코드" in r.content.decode()
    assert ApiClient().get("/admin/qr").status_code == 302


def test_scan_qr_login_then_checkin(clock):
    """붙여 둔 QR을 찍음 → 로그인 화면 → 로그인하면 그 좌석 페이지로 돌아와 바로 예약(=체크인)."""
    seat = Seat.objects.get(no=3)
    path = f"/seat/{seat.no}?t={seat.qr_token}"
    c = ApiClient()
    r = c.get(path)
    assert r.status_code == 302 and r["Location"].startswith("/login?next=")
    assert "좌석 QR을 이용하려면 먼저 로그인해 주세요" in c.get(r["Location"]).content.decode()
    r = c.post("/login", {"student_no": "20260001", "password": "1234", "next": path})
    assert r.status_code == 302 and r["Location"] == path
    d = c.jget(f"/api/seats/{seat.no}?t={seat.qr_token}")
    assert d["qr_ok"] is True and d["page_mode"] == "reserve_now"
    res = c.jpost("/api/reservations", {"seat_no": seat.no, "qr_token": qr_token(seat.no)}).json()
    assert Reservation.objects.get(id=res["id"]).status == "in_use"  # 예약과 동시에 체크인


def test_signup_keeps_qr_next():
    seat = Seat.objects.get(no=3)
    path = f"/seat/{seat.no}?t={seat.qr_token}"
    c = ApiClient()
    assert "/signup?next=" in c.get("/login", {"next": path}).content.decode()
    r = c.post("/signup", {"student_no": "20269999", "name": "새이용자", "password": "pw123456",
                           "password2": "pw123456", "next": path})
    assert r.status_code == 302 and r["Location"] == path


def test_reset_keeps_tokens_helper(tmp_path):
    import sqlite3

    from seats.management.commands.init_db import saved_tokens
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE seats_seat (no INTEGER PRIMARY KEY, qr_token TEXT)")
        conn.executemany("INSERT INTO seats_seat VALUES (?, ?)", [(1, "aaa"), (2, "bbb")])
    assert saved_tokens(db) == {1: "aaa", 2: "bbb"}
    assert saved_tokens(tmp_path / "none.db") == {}
