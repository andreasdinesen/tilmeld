#!/usr/bin/env bash
# Lokal opstart: opretter venv, installerer Flask, starter serveren på port 8080.
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Opretter virtuelt miljø..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

export PORT="${PORT:-8080}"
echo "Starter Tilmeld på http://localhost:$PORT  (master-admin: http://localhost:$PORT/master)"
python app.py
