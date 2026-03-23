#!/bin/bash
# PolicyDB — dev install for testing
# Installs current source as editable package, applies migrations, verifies.
# Works from main repo or any worktree.
#
# Usage:
#   bash scripts/dev-install.sh           # install + migrate
#   bash scripts/dev-install.sh --serve   # install + migrate + start dev server

set -euo pipefail

# Find project root (works from scripts/ dir or project root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -f "$PROJECT_ROOT/pyproject.toml" ]; then
    echo "ERROR: Can't find pyproject.toml at $PROJECT_ROOT"
    exit 1
fi

echo ""
echo "============================================"
echo "  PolicyDB — Dev Install"
echo "============================================"
echo ""
echo "  Project: $PROJECT_ROOT"

# Check if we're in a worktree
if [ -f "$PROJECT_ROOT/.git" ]; then
    echo "  Context: git worktree"
else
    echo "  Context: main repo"
fi

# Install editable
echo ""
echo "  Installing editable package..."
pip install -q -e "$PROJECT_ROOT"

# Apply migrations
echo "  Applying migrations..."
policydb db init 2>/dev/null || true

# Verify
VERSION=$(python -c "from policydb import __version__; print(__version__)")
BINARY=$(which policydb 2>/dev/null || echo "(not on PATH)")

echo ""
echo "============================================"
echo "  Installed policydb v${VERSION}"
echo "============================================"
echo ""
echo "  Binary: $BINARY"
echo "  Source: $PROJECT_ROOT/src/policydb/"
echo ""

if [ "${1:-}" = "--serve" ]; then
    echo "  Starting dev server on port 8001 with auto-reload..."
    echo "  http://127.0.0.1:8001"
    echo ""
    exec policydb serve --port 8001 --reload --open
else
    echo "  To start dev server:"
    echo "    policydb serve --port 8001 --reload"
    echo ""
fi
