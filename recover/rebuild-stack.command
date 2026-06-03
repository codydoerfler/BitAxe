#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# rebuild-stack.command — Step 2 of Pi recovery
# Run AFTER flash-card.command + first boot. SSHes into the freshly-flashed Pi
# and restores the entire stack: dashboard, nginx, cloudflared tunnel,
# Tailscale, Docker + OctoEverywhere — restoring saved credentials so there is
# NO browser re-auth and NO printer re-pairing.
# Double-click to run.
# ─────────────────────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

REPO="/Users/codydoerfler/BitAxe"
BACKUP="$HOME/bitaxe-pi-backup/pi-creds.tgz"
PRINTERS="$HOME/bitaxe-pi-backup/printers.env"
PI_TS="100.94.9.23"      # Tailscale address (works anywhere)
PI_LOCAL="192.168.4.152" # home-network address

echo "════════════════════════════════════════════════════════"
echo "  BitAxe Pi — Rebuild full stack"
echo "════════════════════════════════════════════════════════"

# 1. Find the Pi (try local first, then Tailscale)
PI=""
for cand in "$PI_LOCAL" "$PI_TS"; do
  echo "→ probing $cand ..."
  if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=6 "codydoerfler@$cand" true 2>/dev/null; then PI="$cand"; break; fi
done
[ -z "$PI" ] && { echo "✗ Can't reach the Pi. Is it booted + on WiFi? (Tailscale running on this Mac?)"; read -p "Enter to close"; exit 1; }
echo "✓ Connected to Pi at $PI"
SSH="ssh codydoerfler@$PI"

# 2. Dashboard (from git) + systemd service
echo "→ Deploying dashboard..."
$SSH 'mkdir -p ~/bitaxe'
scp -q "$REPO/index.html" "$REPO/server.py" "codydoerfler@$PI:~/bitaxe/"
$SSH 'cat > ~/bitaxe/config.json << JSON
{"miners": [{"ip": "http://192.168.4.154", "name": "BitAxe 1"}]}
JSON
sudo tee /etc/systemd/system/bitaxe-dashboard.service >/dev/null << UNIT
[Unit]
Description=BitAxe Dashboard
After=network.target
[Service]
ExecStart=/usr/bin/python3 /home/codydoerfler/bitaxe/server.py
WorkingDirectory=/home/codydoerfler/bitaxe
Restart=always
User=codydoerfler
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now bitaxe-dashboard'

# 3. nginx proxy :80 -> :3000
echo "→ Installing nginx..."
$SSH 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx >/dev/null 2>&1
sudo tee /etc/nginx/sites-available/bitaxe >/dev/null << NGINX
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    location / { proxy_pass http://127.0.0.1:3000; proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; }
}
NGINX
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/bitaxe /etc/nginx/sites-enabled/bitaxe
sudo nginx -t && sudo systemctl restart nginx'

# 4. Push the credential backup to the Pi and restore it
echo "→ Restoring saved credentials (cloudflared, tailscale, octoeverywhere)..."
scp -q "$BACKUP" "codydoerfler@$PI:/tmp/pi-creds.tgz"
$SSH 'sudo tar xzf /tmp/pi-creds.tgz -C / && sudo chown -R codydoerfler:codydoerfler ~/.cloudflared ~/octoeverywhere-data 2>/dev/null; rm -f /tmp/pi-creds.tgz'

# 5. cloudflared — install + service (config + creds already restored to /etc/cloudflared)
echo "→ Installing cloudflared tunnel..."
$SSH 'cd /tmp && curl -fsSL -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb && sudo dpkg -i cloudflared.deb >/dev/null 2>&1
sudo cloudflared service install >/dev/null 2>&1 || true
sudo systemctl enable --now cloudflared'

# 6. Tailscale — install + reconnect using restored node state (no re-auth)
echo "→ Installing Tailscale (restoring node identity, no re-auth)..."
$SSH 'curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1
sudo systemctl stop tailscaled 2>/dev/null || true
# restored tailscaled.state is already at /var/lib/tailscale/ from the backup
sudo systemctl enable --now tailscaled
sleep 2; sudo tailscale up --accept-routes 2>&1 | grep -i "http" || echo "  (reconnected with saved identity)"'

# 7. Docker + OctoEverywhere companions (restored data + re-run containers)
echo "→ Installing Docker + OctoEverywhere companions..."
[ -f "$PRINTERS" ] && source "$PRINTERS"
$SSH "command -v docker >/dev/null 2>&1 || (curl -fsSL https://get.docker.com | sudo sh >/dev/null 2>&1; sudo usermod -aG docker codydoerfler; sudo systemctl enable --now docker)"
$SSH "sudo docker rm -f octoeverywhere-p1s octoeverywhere-a1mini 2>/dev/null || true
sudo docker run -d --name octoeverywhere-p1s --restart unless-stopped -e COMPANION_MODE=bambu -e ACCESS_CODE=${P1S_ACCESS} -e SERIAL_NUMBER=${P1S_SERIAL} -e PRINTER_IP=${P1S_IP} -v ~/octoeverywhere-data/p1s:/data octoeverywhere/octoeverywhere >/dev/null 2>&1
sudo docker run -d --name octoeverywhere-a1mini --restart unless-stopped -e COMPANION_MODE=bambu -e ACCESS_CODE=${A1_ACCESS} -e SERIAL_NUMBER=${A1_SERIAL} -e PRINTER_IP=${A1_IP} -v ~/octoeverywhere-data/a1mini:/data octoeverywhere/octoeverywhere >/dev/null 2>&1"

# 8. Verify
echo; echo "════════════ VERIFY ════════════"
$SSH 'echo "dashboard:  $(systemctl is-active bitaxe-dashboard)"
echo "nginx:      $(systemctl is-active nginx)"
echo "cloudflared:$(systemctl is-active cloudflared)"
echo "tailscaled: $(systemctl is-active tailscaled)"
echo "docker:     $(systemctl is-active docker)"
echo "containers: $(sudo docker ps --format "{{.Names}}" | tr "\n" " ")"'
echo "public URL: $(curl -s --max-time 10 -o /dev/null -w "%{http_code}" https://bitaxe.rrwestminster.com/)"
echo "════════════════════════════════"
echo "✓ Rebuild complete."
read -p "Press Enter to close."
