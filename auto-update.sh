#!/bin/bash
# Auto-deploy the BitAxe dashboard on this Mac: check GitHub for new commits on
# the tracked branch and, if there are any, fast-forward the checkout and restart
# the server. Run on a timer by the "…-autoupdate" LaunchAgent (see
# "Install Dashboard Autostart.command"); safe to run by hand too.
#
# Design notes:
#   * Fast-forward only. If the local checkout has diverged (someone hand-edited
#     index.html/server.py on the mini), we DON'T touch it — we log a warning and
#     bail, rather than silently discarding local work. Resolve it by hand, then
#     the next tick resumes.
#   * config.json / history.db are gitignored, so a normal pull never touches them.
#   * Restart goes through the server LaunchAgent's kickstart when present, so the
#     new code is live within one tick of a merge.
set -u
cd "$(dirname "$0")" || exit 1

LABEL="com.doerfler.bitaxe-dashboard"
LOG="$(pwd)/auto-update.log"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG"; }

# Talk to GitHub. Network hiccups are normal on a timer — just skip this tick.
if ! git fetch --quiet origin "$BRANCH" 2>>"$LOG"; then
  log "fetch failed (network?) — skipping this tick"
  exit 0
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH" 2>/dev/null)"

# Already current → nothing to do, stay quiet (no log spam every tick).
[ "$LOCAL" = "$REMOTE" ] && exit 0

log "new commits on $BRANCH: ${LOCAL:0:7} -> ${REMOTE:0:7} — deploying"

# Fast-forward only. If it can't ff, the checkout diverged; leave it for a human.
if ! git merge --ff-only "origin/$BRANCH" >>"$LOG" 2>&1; then
  log "WARNING: cannot fast-forward (local checkout diverged). Left unchanged — resolve by hand."
  exit 1
fi

# Restart the server so the new code is live.
if launchctl list 2>/dev/null | grep -q "$LABEL"; then
  launchctl kickstart -k "gui/$(id -u)/$LABEL" >>"$LOG" 2>&1
  log "restarted via LaunchAgent ($LABEL)"
else
  pkill -f "[s]erver.py" 2>/dev/null || true
  sleep 1
  nohup python3 server.py > dashboard.log 2>&1 &
  log "restarted server.py directly (no LaunchAgent)"
fi

sleep 1
CODE="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000 || echo '???')"
log "deployed ${REMOTE:0:7} — health $CODE"
