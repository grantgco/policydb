#!/bin/bash
# PolicyDB — build a distributable zip for emailing to another Mac.
#
# Usage:
#   ./scripts/make_package.sh                 # Apple Silicon (default)
#   ./scripts/make_package.sh --intel         # Intel Mac
#   ./scripts/make_package.sh --universal     # Both architectures (larger)
#
# Output: PolicyDB.zip in the project root

set -euo pipefail
cd "$(dirname "$0")/.."  # run from project root

PLATFORM="${1:---apple-silicon}"

case "$PLATFORM" in
    --apple-silicon) PLAT="macosx_11_0_arm64"      ;;
    --intel)         PLAT="macosx_10_9_x86_64"     ;;
    --universal)     PLAT="macosx_11_0_universal2" ;;
    *)
        echo "Usage: $0 [--apple-silicon|--intel|--universal]"
        echo "  --apple-silicon  M1/M2/M3/M4 Macs (default)"
        echo "  --intel          Intel Macs"
        echo "  --universal      Works on both (larger zip)"
        exit 1
        ;;
esac

# ── 0. Version bump prompt ───────────────────────────────────────────────────
CURRENT_VERSION=$(python -c "from policydb import __version__; print(__version__)")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"
NEXT_PATCH="${MAJOR}.${MINOR}.$((PATCH + 1))"
NEXT_MINOR="${MAJOR}.$((MINOR + 1)).0"
NEXT_MAJOR="$((MAJOR + 1)).0.0"

echo ""
echo "Current version: v${CURRENT_VERSION}"
echo ""
echo "  1) Patch  → v${NEXT_PATCH}   (bug fixes, small tweaks)"
echo "  2) Minor  → v${NEXT_MINOR}   (new features)"
echo "  3) Major  → v${NEXT_MAJOR}   (breaking changes)"
echo "  4) Keep   → v${CURRENT_VERSION}   (no change)"
echo ""
read -rp "Bump version? [1/2/3/4]: " BUMP_CHOICE

case "${BUMP_CHOICE}" in
    1) NEW_VERSION="$NEXT_PATCH" ;;
    2) NEW_VERSION="$NEXT_MINOR" ;;
    3) NEW_VERSION="$NEXT_MAJOR" ;;
    4|"") NEW_VERSION="$CURRENT_VERSION" ;;
    *)
        echo "Invalid choice. Aborting."
        exit 1
        ;;
esac

if [ "$NEW_VERSION" != "$CURRENT_VERSION" ]; then
    # Update both version files
    sed -i '' "s/__version__ = \".*\"/__version__ = \"${NEW_VERSION}\"/" src/policydb/__init__.py
    sed -i '' "s/^version = \".*\"/version = \"${NEW_VERSION}\"/" pyproject.toml
    echo "Bumped: v${CURRENT_VERSION} → v${NEW_VERSION}"
fi

SRC_VERSION=$(python -c "from policydb import __version__; print(__version__)")
TOML_VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')

echo ""
echo "PolicyDB Package Builder"
echo "========================"
echo "Source version:  v${SRC_VERSION}"
echo "pyproject.toml:  v${TOML_VERSION}"
echo "Target platform: $PLAT"
echo ""

if [ "$SRC_VERSION" != "$TOML_VERSION" ]; then
    echo "ERROR: Version mismatch!"
    echo "  __init__.py says: $SRC_VERSION"
    echo "  pyproject.toml says: $TOML_VERSION"
    echo "  Fix both to match, then re-run."
    exit 1
fi

# ── 1. Build wheel (clean rebuild to ensure latest code) ─────────────────────
echo "[1/5] Building wheel..."
rm -f dist/policydb-*.whl
python -m build --wheel --outdir dist/ 2>/dev/null
WHL=$(ls dist/policydb-*.whl | sort -V | tail -1)
WHL_VERSION=$(basename "$WHL" | sed 's/policydb-\([^-]*\)-.*/\1/')
echo "      Built: $(basename "$WHL")"

if [ "$WHL_VERSION" != "$SRC_VERSION" ]; then
    echo "ERROR: Built wheel version ($WHL_VERSION) doesn't match source ($SRC_VERSION)!"
    echo "  You may have a stale build cache. Try: rm -rf build/ dist/ src/*.egg-info"
    exit 1
fi

# ── 2. Create staging directory ───────────────────────────────────────────────
echo "[2/5] Preparing package..."
STAGING=$(mktemp -d)
WDIR="$STAGING/PolicyDB"
mkdir -p "$WDIR/wheels"

# ── 3. Download dependency wheels (offline install on target) ─────────────────
# Copy the freshly-built policydb wheel first, then download deps for each
# supported Python so ABI-tagged wheels (PyYAML, etc.) match the target Mac.
echo "[3/5] Downloading dependencies for $PLAT..."
cp "$WHL" "$WDIR/wheels/"
echo "      Copied $(basename "$WHL")"
for PYVER in 3.11 3.12 3.13; do
    echo "      Python $PYVER deps..."
    pip download \
        --dest "$WDIR/wheels" \
        --python-version "$PYVER" \
        --platform "$PLAT" \
        --only-binary=:all: \
        "$WHL" 2>/dev/null || true
done
# Deduplicate pure-python wheels (py3-none-any) that got downloaded multiple times
SEEN=""
for f in "$WDIR/wheels"/*-py3-none-any.whl; do
    [ -f "$f" ] || continue
    BASE=$(basename "$f")
    if echo "$SEEN" | grep -qF "$BASE"; then
        rm "$f"
    else
        SEEN="$SEEN $BASE"
    fi
done
echo "      $(ls "$WDIR/wheels" | wc -l | tr -d ' ') wheels total"
echo "      PolicyDB wheel: $(ls "$WDIR/wheels"/policydb-*.whl 2>/dev/null | head -1 | xargs basename 2>/dev/null || echo 'MISSING!')"

# ── 4. Copy installer and docs ────────────────────────────────────────────────
cp scripts/install.sh "$WDIR/"
cp scripts/README.txt "$WDIR/"
chmod +x "$WDIR/install.sh"

# ── 5. Zip ────────────────────────────────────────────────────────────────────
echo "[4/5] Creating zip..."
TIMESTAMP=$(date +"%Y%m%d_%H%M")
OUTPUT="PolicyDB_${TIMESTAMP}.zip"
rm -f "$OUTPUT"
(cd "$STAGING" && zip -qr - PolicyDB) > "$OUTPUT"
rm -rf "$STAGING"

SIZE=$(du -sh "$OUTPUT" | cut -f1)

# ── 6. Copy to OneDrive for cross-computer access ───────────────────────────
ONEDRIVE_DIR="/Users/grantgreeson/Library/CloudStorage/OneDrive-grantg.co/PolicyDB"
if [ -d "$ONEDRIVE_DIR" ]; then
    cp "$OUTPUT" "$ONEDRIVE_DIR/$OUTPUT"
    echo "[sync] Copied to OneDrive: $ONEDRIVE_DIR/$OUTPUT"
else
    mkdir -p "$ONEDRIVE_DIR" 2>/dev/null && cp "$OUTPUT" "$ONEDRIVE_DIR/$OUTPUT" \
        && echo "[sync] Created & copied to OneDrive: $ONEDRIVE_DIR/$OUTPUT" \
        || echo "[sync] OneDrive folder not available — skipped copy."
fi

echo ""
echo "[5/5] Verifying package..."
# Spot-check: unzip to temp, confirm wheel version inside the zip matches
VERIFY_DIR=$(mktemp -d)
unzip -q "$OUTPUT" -d "$VERIFY_DIR"
VERIFY_WHL=$(ls "$VERIFY_DIR"/PolicyDB/wheels/policydb-*.whl 2>/dev/null | head -1)
VERIFY_VER=$(basename "$VERIFY_WHL" | sed 's/policydb-\([^-]*\)-.*/\1/')
rm -rf "$VERIFY_DIR"

if [ "$VERIFY_VER" = "$SRC_VERSION" ]; then
    echo "      Verified: zip contains policydb v${VERIFY_VER}"
else
    echo "      WARNING: zip contains v${VERIFY_VER} but expected v${SRC_VERSION}!"
fi

echo ""
echo "========================================"
echo "  PolicyDB v${SRC_VERSION}  —  $OUTPUT  ($SIZE)"
echo "========================================"
echo ""
echo "Synced to OneDrive for access on your other computer."
echo ""
