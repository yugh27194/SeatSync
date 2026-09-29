#!/usr/bin/env python3
"""좌석별 QR PNG + 인쇄용 HTML 생성.

    python tools/make_qr.py --base-url http://192.168.0.10:5000

qr/seat_<no>.png (URL = {base}/seat/{no}?t={qr_token}) 와 A4 인쇄용 qr/print.html 을 만든다.
qr_token은 DB에 있으므로 먼저 `flask --app app init-db` 를 실행해야 한다.
"""
import argparse
import html
import os
import sqlite3
import sys

import qrcode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description="SeatSync 좌석 QR 생성")
    ap.add_argument("--base-url", required=True, help="예: http://192.168.0.10:5000")
    ap.add_argument("--db", default=os.environ.get("SEATSYNC_DB", os.path.join(ROOT, "seatsync.db")))
    ap.add_argument("--out", default=os.path.join(ROOT, "qr"))
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB 파일이 없습니다: {args.db}\n먼저 `flask --app app init-db`를 실행하세요.", file=sys.stderr)
        return 1
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    seats = conn.execute("SELECT no, label, zone, qr_token FROM seats WHERE active = 1 ORDER BY no").fetchall()
    conn.close()

    base = args.base_url.rstrip("/")
    os.makedirs(args.out, exist_ok=True)
    cards = []
    for s in seats:
        url = f"{base}/seat/{s['no']}?t={s['qr_token']}"
        img = qrcode.make(url, box_size=10, border=2)
        name = f"seat_{s['no']}.png"
        img.save(os.path.join(args.out, name))
        cards.append(
            f"""<div class="card"><div class="label">{html.escape(s['label'])}</div>
<div class="zone">{html.escape(s['zone'] or '')}</div>
<img src="{name}" alt="{html.escape(s['label'])} QR">
<div class="guide">스캔해서 체크인/예약</div><div class="url">{html.escape(url)}</div></div>"""
        )
        print(f"생성: {name}  →  {url}")

    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>SeatSync 좌석 QR</title>
<style>
  @page {{ size: A4; margin: 12mm; }}
  body {{ font-family: "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif; margin: 0; }}
  .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 8mm; }}
  .card {{ border: 1.5px dashed #888; border-radius: 4mm; padding: 6mm; text-align: center; page-break-inside: avoid; }}
  .label {{ font-size: 30pt; font-weight: 800; }}
  .zone {{ color: #555; font-size: 11pt; }}
  img {{ width: 55mm; height: 55mm; margin: 3mm 0; }}
  .guide {{ font-size: 14pt; font-weight: 700; }}
  .url {{ font-size: 7pt; color: #999; word-break: break-all; margin-top: 2mm; }}
  h1 {{ font-size: 14pt; }}
  @media print {{ h1 {{ display: none; }} }}
</style></head><body>
<h1>SeatSync 좌석 QR — 인쇄 후 잘라서 좌석에 부착하세요</h1>
<div class="grid">
{chr(10).join(cards)}
</div></body></html>
"""
    with open(os.path.join(args.out, "print.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"인쇄용 페이지: {os.path.join(args.out, 'print.html')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
