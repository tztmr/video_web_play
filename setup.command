#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi
exec "$PYTHON" scripts/setup_link.py --open
