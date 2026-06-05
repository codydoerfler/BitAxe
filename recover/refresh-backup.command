#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# refresh-backup.command — keep the credential backup current
# Re-saves the Pi's Cloudflare tunnel, Tailscale state, and OctoEverywhere config
# to ~/bitaxe-pi-backup/pi-creds.tgz so recovery never needs a re-auth/re-pair.
# Run this after you change the tunnel, Tailscale, or re-pair a printer.
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo "→ Finding the Pi (LAN or Tailscale)..."
PI=""
for host in 192.168.4.152 100.94.9.23; do
  if ssh -o ConnectTimeout=4 -o BatchMode=yes codydoerfler@"$host" true 2>/dev/null; then PI=$host; break; fi
done
[ -z "$PI" ] && { echo "✗ Could not reach the Pi on LAN or Tailscale."; read -p "Press Enter to close."; exit 1; }
echo "→ Reached Pi at $PI."

echo "→ Packing credentials on the Pi..."
ssh codydoerfler@"$PI" 'sudo tar czf /tmp/pi-creds.tgz -C / etc/cloudflared home/codydoerfler/.cloudflared var/lib/tailscale/tailscaled.state -C /home/codydoerfler octoeverywhere-data 2>/dev/null; sudo chown codydoerfler /tmp/pi-creds.tgz'

mkdir -p "$HOME/bitaxe-pi-backup"
echo "→ Copying to ~/bitaxe-pi-backup/ ..."
scp codydoerfler@"$PI":/tmp/pi-creds.tgz "$HOME/bitaxe-pi-backup/"
ssh codydoerfler@"$PI" 'rm -f /tmp/pi-creds.tgz'

echo "✓ Saved: ~/bitaxe-pi-backup/pi-creds.tgz"
read -p "Press Enter to close."
