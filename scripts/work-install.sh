#!/bin/bash
# PolicyDB — install/update from git on work Mac
# First time: clones, creates venv, installs, sets up shell command
# After that: pulls latest, reinstalls
#
# Usage:
#   bash work-install.sh              # Install or update
#   bash work-install.sh uninstall    # Remove (keeps your data)

set -euo pipefail
cd "$(dirname "$0")/.."

POLICYDB_HOME="$HOME/.policydb"
VENV="$POLICYDB_HOME/venv"
PROJECT_ROOT="$(pwd)"
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
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "  Removed auto-start agent"
    if [ -f "$ZSHRC" ]; then
        sed -i '' '/# ── PolicyDB ──/,/# ── \/PolicyDB ──/d' "$ZSHRC"
        echo "  Removed shell function from ~/.zshrc"
    fi
    rm -rf "$VENV"
    echo "  Removed virtual environment"
    echo ""
    echo "Done. Your data is still at: $POLICYDB_HOME"
    echo "  (Delete that folder to remove everything.)"
    echo ""
    exit 0
fi

# ── Install / Update ─────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  PolicyDB — Git Install"
echo "============================================"
echo ""

# Pull latest if this is a git repo
if [ -d .git ] || [ -f .git ]; then
    BRANCH=$(git branch --show-current)
    echo "  Branch: $BRANCH"
    echo "  Pulling latest..."
    git pull --ff-only 2>/dev/null || echo "  (pull skipped — may need manual merge)"
fi

SRC_VERSION=$(grep '__version__' src/policydb/__init__.py | sed 's/.*"\(.*\)".*/\1/')
echo "  Version: v${SRC_VERSION}"

PY=$(find_python) || {
    echo "ERROR: Python 3.11 or newer is required."
    echo "  Download from: https://www.python.org/downloads/"
    exit 1
}
echo "  Python:  $PY"

# Create venv if needed
if [ ! -d "$VENV" ]; then
    echo "  Creating virtual environment..."
    mkdir -p "$POLICYDB_HOME"
    "$PY" -m venv "$VENV"
fi

# Install from source (editable — picks up code changes without reinstalling)
"$VENV/bin/pip" install --quiet --upgrade pip
echo "  Installing from source (editable)..."
"$VENV/bin/pip" install --quiet -e "$PROJECT_ROOT"

# Verify
INSTALLED_VER=$("$VENV/bin/pip" show policydb 2>/dev/null | grep "^Version:" | awk '{print $2}')
echo "  Installed: v${INSTALLED_VER}"

# Initialize / migrate database
"$VENV/bin/policydb" db init 2>/dev/null || true
echo "  Database ready"

# ── Shell function ─────────────────────────────────────────────────────────────
touch "$ZSHRC"
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
            "$HOME/.policydb/venv/bin/policydb" serve --open
        fi
    elif [ "$1" = "stop" ]; then
        echo "Stopping PolicyDB..."
        pkill -f "policydb serve" 2>/dev/null && echo "Stopped." || echo "Not running."
    elif [ "$1" = "update" ]; then
        echo "Updating PolicyDB..."
        bash "$(cat "$HOME/.policydb/.project_root")/scripts/work-install.sh"
    else
        "$HOME/.policydb/venv/bin/policydb" "$@"
    fi
}
# ── /PolicyDB ──
SHELL

# Save project root so `policydb update` knows where to find the repo
echo "$PROJECT_ROOT" > "$POLICYDB_HOME/.project_root"

echo "  Added 'policydb' command to ~/.zshrc"

# ── LaunchAgent ────────────────────────────────────────────────────────────────
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
    AGENT_OK=false
fi

# ── Start server ───────────────────────────────────────────────────────────────
echo ""
pkill -f "policydb serve" 2>/dev/null || true
sleep 1

echo "  Starting PolicyDB v${INSTALLED_VER}..."
if [ "$AGENT_OK" = true ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
fi
"$VENV/bin/policydb" serve &

for i in $(seq 1 15); do
    if curl -s -o /dev/null http://127.0.0.1:8000 2>/dev/null; then
        break
    fi
    sleep 1
done

echo ""
echo "============================================"
echo "  PolicyDB v${INSTALLED_VER} — ready!"
echo "============================================"
echo ""
echo "  To update later, just run:"
echo "    policydb update"
echo ""

open "http://127.0.0.1:8000"
