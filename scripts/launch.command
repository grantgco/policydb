#!/bin/bash
# PolicyDB — double-click to launch
# Installs on first run, starts the web UI every time.

set -euo pipefail
cd "$(dirname "$0")"

VENV="$HOME/.policydb/venv"
WHEELS="$(pwd)/wheels"

# ── Find Python 3.11+ ─────────────────────────────────────────────────────────
find_python() {
    for py in \
        python3.13 python3.12 python3.11 python3 \
        /opt/homebrew/bin/python3.13 \
        /opt/homebrew/bin/python3.12 \
        /opt/homebrew/bin/python3.11 \
        /usr/local/bin/python3.13 \
        /usr/local/bin/python3.12 \
        /usr/local/bin/python3.11; do
        if command -v "$py" &>/dev/null || [ -x "$py" ]; then
            ok=$("$py" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null || echo False)
            if [ "$ok" = "True" ]; then
                echo "$py"
                return 0
            fi
        fi
    done
    return 1
}

# ── First-time install ────────────────────────────────────────────────────────
if [ ! -f "$VENV/bin/policydb" ]; then
    echo "============================================"
    echo "  PolicyDB — First-time Setup"
    echo "============================================"
    echo ""

    PY=$(find_python) || {
        echo "ERROR: Python 3.11 or newer is required."
        echo ""
        echo "Download it from: https://www.python.org/downloads/"
        echo ""
        read -rp "Press Enter to close..."
        exit 1
    }

    echo "Python: $PY"
    echo "Installing PolicyDB (this takes about 30 seconds)..."
    echo ""

    "$PY" -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet --no-index --find-links="$WHEELS" policydb

    echo "Initializing database..."
    "$VENV/bin/policydb" db init

    echo ""
    echo "============================================"
    echo "  Setup complete! Launching PolicyDB..."
    echo "============================================"
    echo ""
fi

# ── Launch ────────────────────────────────────────────────────────────────────
echo "Starting PolicyDB at http://127.0.0.1:8000"
echo "(Close this window to stop the server)"
echo ""
exec "$VENV/bin/policydb" serve --open-browser
