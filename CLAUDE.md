# BitAxe Dashboard

Web dashboard for monitoring BitAxe miners (and host temp/load, BTC price, history charts).
Runs on the **Mac mini** and is viewed locally, over Tailscale, or publicly via Cloudflare.
(It used to run on a Raspberry Pi 5 — some helper scripts and the systemd unit are Pi
leftovers, now marked LEGACY; see below.)

> One of several separate projects (see `~/dev/PROJECTS.md` for the full map). Related:
> **Pi Forge** (Mac recovery app, `~/dev/PiForge`) and the **BitAxe apps** "Prospector"
> (`~/dev/BitAxeApp`, which now includes the widgets — the old standalone widget repo is
> archived). Keep changes here scoped to the dashboard.

## What's here
- `server.py` — Python `http.server` on **port 3000**. Serves `index.html` and proxies
  miner APIs. Endpoints: `/api/system/info`, `/api/miners`, `/api/history`, `/api/btc-price`,
  `/api/pi-temp`, `/api/tickets`, `/api/energy`, `/api/fun-state`, `/api/miner/<n>/...`.
- `index.html` — the whole UI (single file, ~70 KB). Multi-miner: per-miner cards,
  combined summary, core-voltage card, Identify button.
- `config.json` — miner list (gitignored). Shape:
  `{"miners":[{"ip":"http://192.168.4.154","name":"BitAxe 1"},{"ip":"http://192.168.4.159","name":"BitAxe 2"}]}`
- `history.db` — SQLite chart history + `app_state` key/value (streak, first-seen).
  Gitignored; lives only on the host that runs the server.
- `recover/` — older Terminal recovery toolkit (now superseded by the Pi Forge app).
- **macOS run scripts** (Mac mini): `Start Dashboard.command` (launch now),
  `Install Dashboard Autostart.command` + `com.doerfler.bitaxe-dashboard.plist`
  (install as a login LaunchAgent), `Restart Dashboard.command` (restart after a code update).
- **Pi leftovers (LEGACY):** `Deploy to Pi.command`, `bitaxe-dashboard.service` — from the
  Raspberry Pi era; not used on the Mac mini.

## Miners & hosts
- BitAxe 1: `http://192.168.4.154`  •  BitAxe 2: `http://192.168.4.159`
- Host: the **Mac mini** runs the server on port 3000. _(TODO: confirm its LAN/Tailscale
  address here — the old `raspberrypi.local` / `100.94.9.23` entries were the Pi's.)_
- Public URL: `https://bitaxe.rrwestminster.com` (Cloudflare tunnel → host :3000).

## Run / deploy
The dashboard runs on the Mac mini from this repo checkout, so a "deploy" is just
updating the files in place and restarting the server — no scp.
- Local test: `python3 server.py`, then open `http://localhost:3000`.
- Update + restart on the mini:
  1. `git pull` (or check out the branch you want to run).
  2. Double-click **`Restart Dashboard.command`** (or run it). It kickstarts the
     LaunchAgent if installed, otherwise stops/relaunches `server.py`.
  3. Hard-refresh the browser (⌘⇧R).
- First-time autostart: run **`Install Dashboard Autostart.command`** once to register
  the launchd LaunchAgent (starts at login, relaunches on crash — the mac equivalent of
  the old Pi systemd unit).

## Notes
- Git history lives in this repo on the Mac; the mini runs from a checkout of it.
- `history.db` is included in the Pi Forge credentials backup, so chart history (and the
  fun-card streak/first-seen in `app_state`) survives a rebuild.
