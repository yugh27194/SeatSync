"""좌석 QR 인쇄 파일.

좌석마다 QR 하나(= {서비스 주소}/seat/{no}?t={좌석 토큰})를 만든다. 예: A-1 좌석 QR을 찍으면 A-1 좌석 페이지가 열려
로그인 뒤 그 좌석에 체크인(또는 바로 예약)된다. 토큰은 좌석에 직접 가야만 체크인할 수 있게 하는 비밀 값이다.

저장 폴더(settings.SEATSYNC["QR_DIR"], 기본 ./qr)에 만드는 파일:
  seat_<no>.png      좌석별 QR 카드 (95×69mm, 300dpi) — 한 장씩 따로 출력·교체할 때
  seats_A4.pdf       모든 좌석 카드를 A4 한 장(8석)에 모은 인쇄용 PDF — 실선 없이 점선을 따라 잘라 붙인다
  seats_A4.png       같은 A4 시트의 이미지(미리보기·이미지 인쇄용, 9석 이상이면 seats_A4_2.png …)
  manifest.json      만든 시각·서비스 주소·좌석별 QR 주소 (토큰이 바뀌었는지 확인용)
"""
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import qrcode
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from .timeutil import to_iso

DPI = 300
A4 = (2480, 3508)               # 210×297mm @300dpi
MARGIN = 118                    # 10mm
COLS, ROWS = 2, 4               # A4 한 장에 8석
CARD = ((A4[0] - 2 * MARGIN) // COLS, (A4[1] - 2 * MARGIN) // ROWS)   # 약 95×69mm
QR_PX = 600                     # 약 51mm — 폰 카메라로 30~50cm 거리에서 잘 읽히는 크기
MANIFEST = "manifest.json"
SHEET_PDF = "seats_A4.pdf"

FONT_CANDIDATES = [  # 한글 글꼴 (굵은 것 우선)
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]


def qr_dir():
    return Path(settings.SEATSYNC["QR_DIR"])


def seat_url(seat, base_url):
    return f"{base_url.rstrip('/')}/seat/{seat.no}?t={seat.qr_token}"


def default_base_url(request=None):
    """QR에 넣을 서비스 주소: 설정(SEATSYNC_PUBLIC_URL) → 관리자가 지금 접속한 주소."""
    return settings.SEATSYNC.get("PUBLIC_URL") or (request.build_absolute_uri("/").rstrip("/") if request else "")


def check_base_url(base_url):
    """'https://host[:port]' 형식만 허용. 잘못되면 ValueError."""
    base = (base_url or "").strip().rstrip("/")
    p = urlparse(base)
    if p.scheme not in ("http", "https") or not p.netloc or p.query or p.fragment:
        raise ValueError("서비스 주소는 https://example.com 형식이어야 합니다.")
    return base


def _korean_font_path():
    configured = settings.SEATSYNC.get("QR_FONT")
    for path in ([configured] if configured else []) + FONT_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


class _Fonts:
    def __init__(self):
        self.path = _korean_font_path()

    @property
    def korean(self):
        return self.path is not None

    def get(self, size):
        if self.path:
            try:
                return ImageFont.truetype(self.path, size)
            except OSError:
                self.path = None
        return ImageFont.load_default(size=size)


def _text_center(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _dashed_rect(draw, box, color, dash=24, gap=16, width=3):
    x0, y0, x1, y1 = box
    for x in range(x0, x1, dash + gap):
        draw.line([(x, y0), (min(x + dash, x1), y0)], fill=color, width=width)
        draw.line([(x, y1), (min(x + dash, x1), y1)], fill=color, width=width)
    for y in range(y0, y1, dash + gap):
        draw.line([(x0, y), (x0, min(y + dash, y1))], fill=color, width=width)
        draw.line([(x1, y), (x1, min(y + dash, y1))], fill=color, width=width)


def _qr_image(url):
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    q.add_data(url)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").get_image().convert("RGB").resize(
        (QR_PX, QR_PX), Image.NEAREST)


def card_image(seat, url, fonts=None):
    """좌석 QR 카드 한 장: 왼쪽 QR, 오른쪽 좌석 이름과 안내. 가장자리 점선 = 자르는 선."""
    fonts = fonts or _Fonts()
    w, h = CARD
    img = Image.new("RGB", CARD, "white")
    d = ImageDraw.Draw(img)
    _dashed_rect(d, (2, 2, w - 3, h - 3), (170, 170, 170))
    qx, qy = 60, (h - QR_PX) // 2
    img.paste(_qr_image(url), (qx, qy))
    cx = (qx + QR_PX + w) // 2  # 오른쪽 영역 가운데
    navy, gray = (28, 42, 68), (90, 101, 115)
    _text_center(d, cx, 150, seat.label, fonts.get(150), navy)
    if fonts.korean:
        _text_center(d, cx, 360, "좌석 QR 체크인", fonts.get(40), navy)
        _text_center(d, cx, 430, "로그인 후 찍으면", fonts.get(34), gray)
        _text_center(d, cx, 478, "이 좌석에 체크인돼요", fonts.get(34), gray)
    else:
        _text_center(d, cx, 360, "SCAN TO", fonts.get(44), navy)
        _text_center(d, cx, 420, "CHECK IN", fonts.get(44), navy)
    _text_center(d, cx, h - 150, "SeatSync", fonts.get(44), (47, 111, 223))
    return img


def sheet_images(cards):
    """카드들을 A4(8칸)에 배치. 9석 이상이면 여러 장."""
    pages = []
    per = COLS * ROWS
    for i in range(0, max(len(cards), 1), per):
        page = Image.new("RGB", A4, "white")
        for k, card in enumerate(cards[i:i + per]):
            r, c = divmod(k, COLS)
            page.paste(card, (MARGIN + c * CARD[0], MARGIN + r * CARD[1]))
        pages.append(page)
    return pages


def build(seats, base_url, now, out_dir=None):
    """좌석 QR 파일을 저장 폴더에 새로 만든다(이전에 만든 파일은 지움). manifest(dict)를 돌려준다."""
    base = check_base_url(base_url)
    out = Path(out_dir) if out_dir else qr_dir()
    out.mkdir(parents=True, exist_ok=True)
    old = load_manifest(out)
    for name in (old or {}).get("files", []):
        if _safe_name(name):
            (out / name).unlink(missing_ok=True)

    fonts = _Fonts()
    files, rows, cards = [], [], []
    for seat in seats:
        url = seat_url(seat, base)
        card = card_image(seat, url, fonts)
        name = f"seat_{seat.no}.png"
        card.save(out / name, dpi=(DPI, DPI))
        cards.append(card)
        files.append(name)
        rows.append({"no": seat.no, "label": seat.label, "zone": seat.zone, "url": url, "file": name})

    pages = sheet_images(cards)
    pngs = []
    for i, page in enumerate(pages):
        name = "seats_A4.png" if i == 0 else f"seats_A4_{i + 1}.png"
        page.save(out / name, dpi=(DPI, DPI))
        pngs.append(name)
    pages[0].save(out / SHEET_PDF, save_all=True, append_images=pages[1:], resolution=DPI)
    files = [SHEET_PDF, *pngs, *files]

    manifest = {"generated_at": to_iso(now), "base_url": base, "korean_font": fonts.korean,
                "pages": len(pages), "seats": rows, "files": files}
    (out / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_manifest(out_dir=None):
    path = Path(out_dir or qr_dir()) / MANIFEST
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def outdated(manifest, seats):
    """저장된 QR이 지금 좌석과 다른 이유 목록 (좌석 추가·삭제, 토큰·이름 변경). 같으면 []."""
    if not manifest:
        return []
    saved = {r["no"]: r for r in manifest.get("seats", [])}
    now = {s.no: s for s in seats}
    why = []
    for no, s in now.items():
        r = saved.get(no)
        if r is None:
            why.append(f"{s.label}: 저장된 QR 없음")
        elif r["url"] != seat_url(s, manifest["base_url"]) or r["label"] != s.label:
            why.append(f"{s.label}: 좌석 정보가 바뀜")
    why += [f"{r['label']}: 지금은 없는 좌석" for no, r in saved.items() if no not in now]
    return why


def _safe_name(name):
    return isinstance(name, str) and name == os.path.basename(name) and not name.startswith(".") and name != MANIFEST


def stored_file(name):
    """다운로드할 수 있는 저장 파일 경로(manifest에 있는 파일만). 없으면 None."""
    m = load_manifest()
    if not m or not _safe_name(name) or name not in m.get("files", []):
        return None
    path = qr_dir() / name
    return path if path.is_file() else None
