#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# shutdown-pi.command — graceful, UPS-friendly remote power-down
# Cleanly shuts down the Pi so you can move it or cut power without corrupting
# the boot drive. Wait until the green LED stops blinking before unplugging.
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo "→ Finding the Pi (LAN or Tailscale)..."
PI=""
for host in 192.168.4.152 100.94.9.23; do
  if ssh -o ConnectTimeout=4 -o BatchMode=yes codydoerfler@"$host" true 2>/dev/null; then PI=$host; break; fi
done
[ -z "$PI" ] && { echo "✗ Could not reach the Pi on LAN or Tailscale."; read -p "Press Enter to close."; exit 1; }

echo "→ Reached Pi at $PI."
read -p "Send a clean shutdown to the Pi now? Type YES to confirm: " OK
[ "$OK" = "YES" ] || { echo "Cancelled."; read -p "Press Enter to close."; exit 0; }

ssh codydoerfler@"$PI" 'sudo shutdown -h now' 2>/dev/null || true
echo
echo "✓ Shutdown sent. Wait ~15 seconds until the green LED stops blinking,"
echo "  then it is safe to unplug. The dashboard will be offline until it boots again."
read -p "Press Enter to close."
