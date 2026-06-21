# BitAxe Dashboard

Web dashboard for monitoring BitAxe miners (and BTC price, history charts).
Runs on Cody's Mac mini (the Raspberry Pi 5 it used to run on is retired) and is
viewed locally or publicly via Cloudflare.

> One of three separate projects. The others live in **iCloud Drive** under
> `~/Library/Mobile Documents/com~apple~CloudDocs/Documents/Xcode/` — **Pi Forge**
> (Mac recovery app, `.../PiForge`) and the **BitAxe Widget** (`.../Bitaxe Wiget`,
> largely superseded by the `BitAxeWidget` target inside `.../BitAxeApp`, which also
> holds the native macOS app `BitAxeMac`). Each is its own repo. Keep changes here
> scoped to the dashboard.

## What's here
- `server.py` — Python `http.server` on **port 3000** (pure stdlib, no deps). Serves
  `index.html` and proxies miner APIs. Endpoints: `/api/system/info`, `/api/miners`,
  `/api/history`, `/api/btc-price`, `/api/host-load`, `/api/pi-temp`, `/api/tickets`,
  `/api/energy`, `/api/miner/<n>/...`. (`/api/host-load` = Mac mini CPU load, drives the
  dashboard's host-health card. `/api/pi-temp` is legacy/Linux-only — 503s on macOS — kept
  only because the native apps still call it.)
- `backup-history.sh` + `com.codydoerfler.bitaxe-backup` launchd agent — daily 03:30
  `sqlite3 .backup` of `history.db` into iCloud Drive
  (`~/Library/Mobile Documents/com~apple~CloudDocs/BitAxe-backups/`, keeps 14 days). This is
  the off-machine backup so chart history survives a Mac mini wipe (the Pi had none).
- `index.html` — the whole UI (single file, ~70 KB). Multi-miner: per-miner cards,
  combined summary, core-voltage card, Identify button.
- `config.json` — miner list (gitignored). Shape:
  `{"miners":[{"ip":"http://192.168.4.154","name":"BitAxe 1"},{"ip":"http://192.168.4.159","name":"BitAxe 2"}]}`
- `history.db` — SQLite chart history (gitignored; lives only on the Mac mini now; lost
  when the Pi was retired, so chart history restarted from June 2026).
- `recover/`, `Deploy to Pi.command`, `bitaxe-dashboard.service` — leftover Pi-era
  tooling, no longer used now that the dashboard runs directly from this repo checkout.

## Miners & hosts
- BitAxe 1: `http://192.168.4.154`  •  BitAxe 2: `http://192.168.4.159`
- Host: Cody's Mac mini, runs `server.py` directly from this checkout at
  `/Users/codydoerfler/BitAxe` (no more scp/Pi deploy step).
- Public URL: `https://bitaxe.rrwestminster.com` (Cloudflare tunnel `bitaxe` → `localhost:3000`)

## Run / deploy
- Local test: `python3 server.py` then open `http://localhost:3000`.
- Production: runs as a launchd agent, so editing files in this checkout and restarting
  the agent IS the deploy step — no scp/ssh needed anymore:
  `launchctl kickstart -k gui/$(id -u)/com.codydoerfler.bitaxe`
- launchd agents (in `~/Library/LaunchAgents/`):
  - `com.codydoerfler.bitaxe.plist` (`RunAtLoad`+`KeepAlive`) — runs `python3 server.py`
    from this repo on :3000.
  - `com.codydoerfler.cloudflared-bitaxe.plist` (`RunAtLoad`+`KeepAlive`) — runs the
    `bitaxe` cloudflared tunnel, auth'd via a connector token at `~/.cloudflared/bitaxe-token`
    (not the original Pi-era credentials file, which was lost with the Pi), ingress config at
    `~/.cloudflared/config-bitaxe.yml`.
  - `com.codydoerfler.bitaxe-backup.plist` (`RunAtLoad` + daily 03:30) — runs
    `backup-history.sh`.
- **These are LaunchAgents, so they only start at GUI login, not at boot.** For unattended
  reboot/power-loss recovery the Mac mini relies on: auto-login enabled (System Settings →
  Users & Groups), `pmset autorestart 1` (power on after a power failure), and `pmset sleep 0`
  (never sleep — no longer dependent on Amphetamine). Verify with
  `pmset -g | grep -E 'autorestart|sleep '` and `defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser`.

## Notes
- The old "Pi CPU Temp" card was repurposed to a **"Mac mini Load"** card backed by
  `/api/host-load` (CPU 1-min load average as a percent of cores). A real CPU temperature on
  Apple Silicon needs root/IOKit, so load is used instead — reliable, no sudo, stdlib only.
  `/api/pi-temp` still exists (Linux-only, 503s on macOS) for the native apps.
- The same Mac also hosts the PrintWatch dashboard via a sibling cloudflared tunnel
  (`com.codydoerfler.cloudflared-printwatch.plist`); each project keeps its own tunnel.
