#!/bin/bash
# Install (or update) the BitAxe dashboard LaunchAgents on this Mac:
#   1. the SERVER agent  — starts server.py at login, relaunches it if it exits
#   2. the AUTO-UPDATE agent — every 2 min, pulls new commits from GitHub and
#      restarts the server, so merging on GitHub goes live with no manual step
# (the Mac-mini replacement for the old Pi systemd unit). Safe to re-run — e.g.
# after moving the repo or to turn on auto-update. Double-click it, or run from
# a Terminal.
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3 || true)"
SERVER_LABEL="com.doerfler.bitaxe-dashboard"
UPDATE_LABEL="com.doerfler.bitaxe-dashboard-autoupdate"

[ -n "$PYTHON" ] || { echo "python3 not found on PATH — install it, then re-run."; exit 1; }
[ -f "$REPO_DIR/server.py" ] || { echo "server.py not next to this script ($REPO_DIR)"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents"

# Install one agent: sed the placeholders into ~/Library/LaunchAgents and reload.
install_agent() {
  local label="$1" dest="$HOME/Library/LaunchAgents/$1.plist"
  sed -e "s#{{PYTHON}}#$PYTHON#g" -e "s#{{REPO_DIR}}#$REPO_DIR#g" \
      "$REPO_DIR/$label.plist" > "$dest"
  launchctl bootout   "gui/$(id -u)/$label" 2>/dev/null || true   # ignore "not loaded"
  launchctl bootstrap "gui/$(id -u)" "$dest"
  launchctl enable    "gui/$(id -u)/$label"
  echo "Installed: $dest"
}

install_agent "$SERVER_LABEL"
install_agent "$UPDATE_LABEL"

echo "Python:    $PYTHON"
echo "Repo:      $REPO_DIR"
echo "Auto-update: on (checks GitHub every 2 min → $REPO_DIR/auto-update.log)"
sleep 1
printf "Health:    "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000 || echo "no response yet — check $REPO_DIR/dashboard.log"
echo "Dashboard → http://localhost:3000"
