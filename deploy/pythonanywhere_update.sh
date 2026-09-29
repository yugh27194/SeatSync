#!/usr/bin/env bash
# 새 코드 반영:  cd ~/SeatSync && bash deploy/pythonanywhere_update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="$HOME/.virtualenvs/seatsync"
git pull --ff-only
"$VENV/bin/pip" install -q -r requirements.txt
"$VENV/bin/python" manage.py migrate
"$VENV/bin/python" deploy/pythonanywhere_deploy.py --reload-only
