#!/usr/bin/env bash
# Executa o ATENA no Linux/macOS a partir do código-fonte.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m atena "$@"
