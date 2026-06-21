#!/bin/bash
# Daily off-machine backup of the dashboard's SQLite history.
# Uses sqlite3 ".backup" (a safe hot copy of the live DB) into iCloud Drive,
# so chart history survives a Mac mini failure/wipe — the failure mode that
# lost the original history.db when the Pi died. Keeps the last 14 days.
set -euo pipefail

DB="/Users/codydoerfler/BitAxe/history.db"
DEST="$HOME/Library/Mobile Documents/com~apple~CloudDocs/BitAxe-backups"
KEEP=14

mkdir -p "$DEST"
stamp=$(date +%Y%m%d)
out="$DEST/history-$stamp.db"

# Back up to a space-free temp path first, then move into place (iCloud path has spaces).
tmp=$(mktemp /tmp/bitaxe-history.XXXXXX.db)
/usr/bin/sqlite3 "$DB" ".backup '$tmp'"
mv -f "$tmp" "$out"

# Prune: keep only the most recent $KEEP daily backups.
ls -t "$DEST"/history-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do
  rm -f "$f"
done

echo "$(date '+%Y-%m-%d %H:%M:%S')  backed up -> $out  ($(du -h "$out" | cut -f1))"
