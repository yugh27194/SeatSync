"""좌석 QR 인쇄 파일: 좌석별 PNG + A4 PDF 저장, 관리자만 내려받기, QR → 로그인 → 그 좌석 체크인."""
from pathlib import Path

from conftest import ApiClient, qr_token
from django.conf import settings
from django.core.management import call_command
from PIL import Image

from seats import qr
from seats.models import AdminLog, Reservation, Seat

BASE = "https://seatsync.example.com"


def qr_dir():
    return Path(settings.SEATSYNC["QR_DIR"])


def build_via_page(admin, base=BASE):
    return admin.post("/admin/qr", {"base_url": base})


def test_build_files(clock):
    seats = list(Seat.objects.filter(active=True).order_by("no"))
    m = qr.build(seats, BASE + "/", clock())
    assert m["base_url"] == BASE and len(m["seats"]) == len(seats) == 20
    assert m["pages"] == 3  # A4 한 장에 8석
    files = set(p.name for p in qr_dir().iterdir())
    assert {"manifest.json", "seats_A4.pdf", "seats_A4.png", "seats_A4_2.png", "seats_A4_3.png", "seat_1.png"} <= files
    first = m["seats"][0]
    assert first["url"] == f"{BASE}/seat/{seats[0].no}?t={seats[0].qr_token}"  # A-1 QR → A-1 좌석
    with Image.open(qr_dir() / "seats_A4.png") as im:
        assert im.size == qr.A4  # 210×297mm @300dpi
    with Image.open(qr_dir() / "seat_1.png") as im:
        assert im.size == qr.CARD
    assert (qr_dir() / "seats_A4.pdf").read_bytes().count(b"/Type /Page\n") == 3


def test_outdated_after_token_change(clock):
    seats = list(Seat.objects.filter(active=True).order_by("no"))
    m = qr.build(seats, BASE, clock())
    assert qr.outdated(m, seats) == []
    s = seats[0]
    s.qr_token = "new-token"
    assert qr.outdated(m, seats) == [f"{s.label}: 좌석 정보가 바뀜"]


def test_rebuild_removes_old_files(clock):
    seats = list(Seat.objects.filter(active=True).order_by("no"))
    qr.build(seats, BASE, clock())
    qr.build(seats[:8], BASE, clock())
    names = set(p.name for p in qr_dir().iterdir())
    assert "seats_A4_2.png" not in names and "seat_20.png" not in names


def test_bad_base_url(clock):
    for bad in ("", "seatsync.example.com", "ftp://x.com", "https://x.com/?a=1"):
        try:
            qr.build([], bad, clock())
        except ValueError:
            continue
        raise AssertionError(bad)


def test_admin_page_build_and_download(admin):
    r = admin.get("/admin/qr")
    assert r.status_code == 200 and "QR 파일 만들기" in r.content.decode()
    r = build_via_page(admin)
    assert r.status_code == 302 and r["Location"] == "/admin/qr?built=1"
    page = admin.get("/admin/qr?built=1").content.decode()
    assert "A4 한 장 PDF 내려받기" in page and BASE in page
    pdf = admin.get("/admin/qr/files/seats_A4.pdf?download=1")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert "attachment" in pdf["Content-Disposition"]
    assert b"".join(pdf.streaming_content).startswith(b"%PDF")
    assert admin.get("/admin/qr/files/seat_3.png")["Content-Type"] == "image/png"
    assert AdminLog.objects.filter(action="qr_build").count() == 1


def test_admin_page_bad_url_shows_error(admin):
    r = build_via_page(admin, "not a url")
    assert r.status_code == 200 and "https://example.com 형식" in r.content.decode()
    assert not qr_dir().exists() or not any(qr_dir().iterdir())


def test_files_admin_only_and_safe(admin, user):
    build_via_page(admin)
    assert user.get("/admin/qr/files/seats_A4.pdf").status_code == 403  # 좌석 토큰이 들어 있어 관리자만
    assert ApiClient().get("/admin/qr/files/seats_A4.pdf").status_code == 302  # 로그인 필요
    assert admin.get("/admin/qr/files/manifest.json").status_code == 404
    assert admin.get("/admin/qr/files/..%2Fsettings.py").status_code == 404
    assert admin.get("/admin/qr/files/nothing.png").status_code == 404


def test_page_warns_when_outdated(admin):
    build_via_page(admin)
    Seat.objects.filter(no=1).update(qr_token="rotated")
    assert "저장된 QR이 지금 좌석 정보와 다릅니다" in admin.get("/admin/qr").content.decode()


def test_make_qr_command(tmp_path):
    out = tmp_path / "out"
    call_command("make_qr", "--base-url", BASE, "--out", str(out))
    assert (out / "seats_A4.pdf").exists() and qr.load_manifest(out)["base_url"] == BASE


def test_scan_qr_login_then_checkin(clock):
    """출력한 QR을 찍음 → 로그인 화면 → 로그인하면 그 좌석 페이지로 돌아와 바로 예약(=체크인)."""
    seat = Seat.objects.get(no=3)
    path = f"/seat/{seat.no}?t={seat.qr_token}"
    c = ApiClient()
    r = c.get(path)
    assert r.status_code == 302 and r["Location"].startswith("/login?next=")
    login_page = c.get(r["Location"]).content.decode()
    assert "좌석 QR을 이용하려면 먼저 로그인해 주세요" in login_page
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
    page = c.get("/login", {"next": path}).content.decode()
    assert "/signup?next=" in page  # 처음 온 이용자도 가입 후 그 좌석으로 돌아온다
    r = c.post("/signup", {"student_no": "20269999", "name": "새이용자", "password": "pw123456",
                           "password2": "pw123456", "next": path})
    assert r.status_code == 302 and r["Location"] == path


# ---------------------------------------------------------------- 이미 출력한 QR이 계속 쓰이는지

def test_printed_qr_url_unchanged(admin, clock):
    """예전 /admin/qr 화면에서 출력한 QR(= {접속 주소}/seat/{no}?t={토큰})과 같은 주소를 만든다."""
    seat = Seat.objects.get(no=1)
    legacy = f"http://testserver/seat/{seat.no}?t={seat.qr_token}"  # 예전: request.build_absolute_uri(...)
    assert legacy in admin.get("/admin/qr").content.decode()
    assert qr.seat_url(seat, "http://testserver") == legacy


def test_tokens_survive_reseed_and_qr_build(clock):
    before = dict(Seat.objects.values_list("no", "qr_token"))
    from seats.seed import seed
    seed(now=clock())
    qr.build(list(Seat.objects.filter(active=True)), BASE, clock())
    assert dict(Seat.objects.values_list("no", "qr_token")) == before


def test_reset_keeps_tokens_helper(tmp_path):
    import sqlite3

    from seats.management.commands.init_db import saved_tokens
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE seats_seat (no INTEGER PRIMARY KEY, qr_token TEXT)")
        conn.executemany("INSERT INTO seats_seat VALUES (?, ?)", [(1, "aaa"), (2, "bbb")])
    assert saved_tokens(db) == {1: "aaa", 2: "bbb"}
    assert saved_tokens(tmp_path / "none.db") == {}
