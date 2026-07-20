#!/bin/bash
# Restart the BitAxe dashboard on this Mac after updating the code. Works either
# way it's running: if the LaunchAgent is installed it gets kickstarted;
# otherwise any stray "python3 server.py" is stopped and relaunched. Double-click
# it, or run it from a Terminal.
set -e
cd "$(dirname "$0")"
LABEL="com.doerfler.bitaxe-dashboard"

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
  echo "launchd-managed → kickstarting $LABEL"
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
else
  echo "Not launchd-managed → stopping and relaunching server.py"
  pkill -f "[s]erver.py" 2>/dev/null || true
  sleep 1
  nohup python3 server.py > dashboard.log 2>&1 &
  echo "Started (logs: $(pwd)/dashboard.log)"
fi

sleep 1
printf "Health: "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000 || echo "no response yet"
echo "Dashboard → http://localhost:3000  (hard-refresh the browser: Cmd-Shift-R)"
