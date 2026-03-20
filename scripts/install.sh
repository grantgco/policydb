#!/bin/bash
# PolicyDB — install from Terminal
# Bypasses Gatekeeper / JAMF restrictions entirely.
#
# Usage:
#   bash install.sh              Install or upgrade PolicyDB
#   bash install.sh uninstall    Remove PolicyDB (keeps your data)

set -euo pipefail
cd "$(dirname "$0")"

POLICYDB_HOME="$HOME/.policydb"
VENV="$POLICYDB_HOME/venv"
WHEELS="$(pwd)/wheels"
LABEL="com.policydb.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ZSHRC="$HOME/.zshrc"

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

# ── Uninstall ──────────────────────────────────────────────────────────────────
if [ "${1:-}" = "uninstall" ]; then
    echo ""
    echo "PolicyDB — Uninstall"
    echo "===================="
    echo ""

    # Stop and unload LaunchAgent
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "  Removed auto-start agent"

    # Remove shell function from .zshrc
    if [ -f "$ZSHRC" ]; then
        sed -i '' '/# ── PolicyDB ──/,/# ── \/PolicyDB ──/d' "$ZSHRC"
        echo "  Removed shell function from ~/.zshrc"
    fi

    # Remove venv (but keep data)
    rm -rf "$VENV"
    echo "  Removed virtual environment"

    echo ""
    echo "Done. Your data is still at: $POLICYDB_HOME"
    echo "  (Delete that folder to remove everything.)"
    echo ""
    exit 0
fi

# ── Install / Upgrade ─────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  PolicyDB — Install"
echo "============================================"
echo ""

PY=$(find_python) || {
    echo "ERROR: Python 3.11 or newer is required."
    echo ""
    echo "  Download it from: https://www.python.org/downloads/"
    echo "  Install it, then run this script again."
    echo ""
    exit 1
}
echo "  Python:  $PY"

# Create / update venv
UPGRADE=false
if [ -d "$VENV" ]; then
    echo "  Upgrading existing install..."
    UPGRADE=true
else
    echo "  Creating virtual environment..."
    mkdir -p "$POLICYDB_HOME"
    "$PY" -m venv "$VENV"
fi

# Show what we're installing
PKG_WHL=$(ls "$WHEELS"/policydb-*.whl 2>/dev/null | head -1)
PKG_VER=$(basename "$PKG_WHL" | sed 's/policydb-\([^-]*\)-.*/\1/')
echo "  Package version: v${PKG_VER}"

"$VENV/bin/pip" install --quiet --upgrade pip
if [ "$UPGRADE" = true ]; then
    OLD_VER=$("$VENV/bin/pip" show policydb 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "none")
    echo "  Currently installed: v${OLD_VER}"
    echo "  Removing old version..."
    "$VENV/bin/pip" uninstall -y policydb 2>/dev/null || true
fi

"$VENV/bin/pip" install --quiet --no-index --find-links="$WHEELS" policydb

# Verify install
INSTALLED_VER=$("$VENV/bin/pip" show policydb 2>/dev/null | grep "^Version:" | awk '{print $2}')
if [ "$INSTALLED_VER" != "$PKG_VER" ]; then
    echo ""
    echo "  WARNING: Version mismatch!"
    echo "    Package wheel: v${PKG_VER}"
    echo "    Installed:     v${INSTALLED_VER}"
    echo "  Try: $VENV/bin/pip install --no-index --find-links=$WHEELS --force-reinstall policydb"
    echo ""
else
    echo "  Installed PolicyDB v${INSTALLED_VER}"
fi

# Initialize / migrate database
"$VENV/bin/policydb" db init 2>/dev/null
echo "  Database ready"

# ── Shell function ─────────────────────────────────────────────────────────────
touch "$ZSHRC"
# Remove old block if present, then add fresh
sed -i '' '/# ── PolicyDB ──/,/# ── \/PolicyDB ──/d' "$ZSHRC"

cat >> "$ZSHRC" <<'SHELL'
# ── PolicyDB ──
policydb() {
    if [ $# -eq 0 ]; then
        if curl -s -o /dev/null -w '' http://127.0.0.1:8000 2>/dev/null; then
            echo "PolicyDB is running — opening browser..."
            open "http://127.0.0.1:8000"
        else
            echo "Starting PolicyDB..."
            "$HOME/.policydb/venv/bin/policydb" serve --open-browser
        fi
    elif [ "$1" = "stop" ]; then
        echo "Stopping PolicyDB..."
        pkill -f "policydb serve" 2>/dev/null && echo "Stopped." || echo "Not running."
    else
        "$HOME/.policydb/venv/bin/policydb" "$@"
    fi
}
# ── /PolicyDB ──
SHELL

echo "  Added 'policydb' command to ~/.zshrc"

# ── LaunchAgent (auto-start on login) ─────────────────────────────────────────
# Unload old agent if present
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/policydb</string>
    <string>serve</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <false/>

  <key>StandardOutPath</key>
  <string>$POLICYDB_HOME/server.log</string>

  <key>StandardErrorPath</key>
  <string>$POLICYDB_HOME/server.log</string>
</dict>
</plist>
EOF

if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
    echo "  Installed auto-start agent"
    AGENT_OK=true
else
    echo "  Auto-start agent could not be installed (JAMF restriction — this is OK)"
    echo "  You can start PolicyDB manually by typing 'policydb' in Terminal"
    AGENT_OK=false
fi

# ── Stop old server, start fresh ──────────────────────────────────────────────
echo ""
echo "  Stopping any running PolicyDB server..."
pkill -f "policydb serve" 2>/dev/null || true
sleep 1

echo "  Starting PolicyDB v${INSTALLED_VER}..."
if [ "$AGENT_OK" = true ]; then
    # Reload the agent so it picks up the new binary
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
fi
# Always start directly as well (agent may not work on JAMF-managed Macs)
"$VENV/bin/policydb" serve &
SERVER_PID=$!

echo "  Waiting for server..."
for i in $(seq 1 15); do
    if curl -s -o /dev/null http://127.0.0.1:8000 2>/dev/null; then
        break
    fi
    sleep 1
done

echo ""
echo "============================================"
echo "  Install complete!"
echo "============================================"
echo ""
echo "  From now on:"
if [ "$AGENT_OK" = true ]; then
echo "    - Server auto-starts when you log in"
fi
echo "    - Type 'policydb' in any Terminal to open it"
echo "    - Type 'policydb stop' to stop the server"
echo ""
echo "  Your data lives at: ~/.policydb/"
echo ""

open "http://127.0.0.1:8000"
