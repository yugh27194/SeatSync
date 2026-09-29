#!/usr/bin/env bash
# SeatSync를 외부 링크(https://....trycloudflare.com)로 공유한다 (macOS/Linux). Ctrl+C로 종료.
cd "$(dirname "$0")"
command -v cloudflared >/dev/null || { echo "cloudflared가 필요합니다: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"; exit 1; }
[ -f .venv/bin/activate ] && . .venv/bin/activate
python manage.py serve --port 5000 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 4
echo "잠시 후 나오는 https://xxxx.trycloudflare.com 주소를 공유하세요. (끄려면 Ctrl+C)"
cloudflared tunnel --url http://localhost:5000
