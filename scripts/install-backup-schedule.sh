#!/bin/bash
# Install or uninstall the PolicyDB daily backup launchd job.
# Usage:
#   ./install-backup-schedule.sh install
#   ./install-backup-schedule.sh uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/policydb-backup.sh"
PLIST_DEST="$HOME/Library/LaunchAgents/com.policydb.backup.plist"
LABEL="com.policydb.backup"

case "${1:-}" in
  install)
    mkdir -p "$HOME/.policydb/backups"
    mkdir -p "$HOME/Library/LaunchAgents"

    # Unload old agent if present
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

    # Generate plist with correct paths (don't copy static template)
    cat > "$PLIST_DEST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>

  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$BACKUP_SCRIPT</string>
  </array>

  <!-- Run daily at 2:00 AM -->
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>2</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>

  <key>RunAtLoad</key>
  <false/>

  <key>StandardOutPath</key>
  <string>$HOME/.policydb/backups/backup.log</string>

  <key>StandardErrorPath</key>
  <string>$HOME/.policydb/backups/backup.log</string>

</dict>
</plist>
EOF

    if launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST" 2>/dev/null; then
      echo "Installed. PolicyDB will back up daily at 2:00 AM."
    else
      echo "Installed plist but could not load agent (JAMF restriction — this is OK)."
      echo "The backup will run next time you log in."
    fi
    echo "Backups saved to: $HOME/.policydb/backups/"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "Uninstalled backup schedule."
    ;;
  *)
    echo "Usage: $0 install | uninstall"
    exit 1
    ;;
esac
