#!/usr/bin/env bash
# PythonAnywhere Bash 콘솔에서 처음 한 번:  cd ~/SeatSync && bash deploy/pythonanywhere_setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-python3.11}"
VENV="$HOME/.virtualenvs/seatsync"
echo "[1/4] 가상환경 ($VENV)"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
echo "[2/4] 패키지 설치"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt
echo "[3/4] DB 준비"
"$VENV/bin/python" manage.py init_db
echo "[4/4] 웹 앱 설정 (API 토큰 필요)"
"$VENV/bin/python" deploy/pythonanywhere_deploy.py "$@"
