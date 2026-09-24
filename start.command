#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi
echo 'TACO小剧场 → http://127.0.0.1:8787'
exec "$PYTHON" server.py
