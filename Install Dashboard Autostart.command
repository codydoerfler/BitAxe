#!/bin/bash
# Install (or update) the BitAxe dashboard as a macOS LaunchAgent so it starts
# at login and relaunches if it exits — the Mac-mini replacement for the old Pi
# systemd unit. Safe to re-run (e.g. after moving the repo). Double-click it, or
# run it from a Terminal.
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3 || true)"
LABEL="com.doerfler.bitaxe-dashboard"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -n "$PYTHON" ] || { echo "python3 not found on PATH — install it, then re-run."; exit 1; }
[ -f "$REPO_DIR/server.py" ] || { echo "server.py not next to this script ($REPO_DIR)"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#{{PYTHON}}#$PYTHON#g" -e "s#{{REPO_DIR}}#$REPO_DIR#g" \
    "$REPO_DIR/com.doerfler.bitaxe-dashboard.plist" > "$DEST"

# Reload cleanly (ignore "not loaded" on first install).
launchctl bootout   "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl enable    "gui/$(id -u)/$LABEL"

echo "Installed: $DEST"
echo "Python:    $PYTHON"
echo "Repo:      $REPO_DIR"
sleep 1
printf "Health:    "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000 || echo "no response yet — check $REPO_DIR/dashboard.log"
echo "Dashboard → http://localhost:3000"
