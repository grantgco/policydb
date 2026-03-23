#!/bin/bash
# PolicyDB daily backup script
# Schedule via launchd (see scripts/com.policydb.backup.plist)
# or cron: 0 2 * * * /path/to/policydb-backup.sh

set -euo pipefail

# Use the venv that install.sh creates, fall back to PATH
POLICYDB_BIN="${HOME}/.policydb/venv/bin/policydb"
if [ ! -x "$POLICYDB_BIN" ]; then
  POLICYDB_BIN="$(which policydb 2>/dev/null || echo "")"
fi

if [ ! -x "$POLICYDB_BIN" ]; then
  echo "ERROR: policydb not found. Check POLICYDB_BIN in this script." >&2
  exit 1
fi

"$POLICYDB_BIN" db backup --keep 30

# Exit 0 so launchd doesn't report failure on prune-only runs
exit 0
