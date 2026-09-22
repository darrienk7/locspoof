#!/usr/bin/env bash
# locspoof launcher for macOS and Linux.
#
# First run creates a private Python environment in .venv and installs what
# locspoof needs. After that it just starts the app. If requirements.txt
# changes (for example after a `git pull`), it reinstalls automatically.

set -euo pipefail
cd "$(dirname "$0")"

VENV_PY=".venv/bin/python"
STAMP=".venv/requirements.installed"

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if [ ! -x "$VENV_PY" ]; then
    echo "First run - setting up locspoof. This takes a minute, once."
    if ! PYTHON="$(find_python)"; then
        echo
        echo "Python 3.10 or newer is required and wasn't found."
        echo "Install it from https://www.python.org/downloads/ and run this again."
        exit 1
    fi
    "$PYTHON" -m venv .venv
fi

if ! cmp -s requirements.txt "$STAMP" 2>/dev/null; then
    echo "Installing dependencies..."
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install --quiet -r requirements.txt
    cp requirements.txt "$STAMP"
    echo "Done."
    echo
fi

exec "$VENV_PY" main.py "$@"
