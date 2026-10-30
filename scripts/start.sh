#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt -c requirements-lock.txt
printf '%s\n' 'Workshop Pulse: http://localhost:8000; use LAN IP on phones. Ctrl+C to stop.'
exec .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
