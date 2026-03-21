#!/bin/bash
# Install or uninstall the PolicyDB daily backup launchd job.
# Usage:
#   ./install-backup-schedule.sh install
#   ./install-backup-schedule.sh uninstall

PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/com.policydb.backup.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.policydb.backup.plist"
LABEL="com.policydb.backup"

case "${1:-}" in
  install)
    mkdir -p "$HOME/.policydb/backups"
    cp "$PLIST_SRC" "$PLIST_DEST"
    launchctl load "$PLIST_DEST"
    echo "Installed. PolicyDB will back up daily at 2:00 AM."
    echo "Backups saved to: $HOME/.policydb/backups/"
    ;;
  uninstall)
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "Uninstalled backup schedule."
    ;;
  *)
    echo "Usage: $0 install | uninstall"
    exit 1
    ;;
esac
