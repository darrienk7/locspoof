#!/usr/bin/env bash
# locspoof launcher (macOS / Linux)
#
# On macOS no sudo is needed: pymobiledevice3 uses Apple's native tunnel.
# On Linux the classic tunnel needs root, so main.py will ask you to re-run
# under sudo.

set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "Could not find $PY"
    echo "Create the venv first:"
    echo "    python3 -m venv .venv && .venv/bin/pip install pymobiledevice3"
    exit 1
fi

exec "$PY" main.py "$@"
