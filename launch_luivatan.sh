#!/usr/bin/env bash
# Apex AI launcher (kept under the historical name for compatibility).
set -e
cd "$(dirname "$0")"
if [ -f .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python3
fi
exec "$PY" ui.py
