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
- **Auto-update** (Mac mini): `auto-update.sh` + `com.doerfler.bitaxe-dashboard-autoupdate.plist`
  — a second LaunchAgent that polls GitHub every 2 min and, when new commits land on
  the tracked branch, fast-forwards the checkout and restarts the server. This is what
  makes "merge on GitHub" go live with no manual step. Installed by the same
  `Install Dashboard Autostart.command`. Logs to `auto-update.log`.
- **Pi leftovers (LEGACY):** `Deploy to Pi.command`, `bitaxe-dashboard.service` — from the
  Raspberry Pi era; not used on the Mac mini.

## Miners & hosts
- BitAxe 1: `http://192.168.4.154`  •  BitAxe 2: `http://192.168.4.159`
- Host: the **Mac mini** (`codys-mac-mini`) runs the server on port 3000.
  Tailscale `100.64.112.105` (MagicDNS: `codys-mac-mini`). _(The old
  `raspberrypi.local` / `100.94.9.23` / `100.80.87.42` entries were the Pi's.)_
- Public URL: `https://bitaxe.rrwestminster.com` (Cloudflare tunnel → host :3000).

## Run / deploy
The dashboard runs on the Mac mini from this repo checkout, so a "deploy" is just
updating the files in place and restarting the server — no scp.

**Once auto-update is installed (see below), there's nothing to do:** merge to the
tracked branch on GitHub and within ~2 min the mini pulls it and restarts itself.
Then hard-refresh the browser (⌘⇧R). The manual paths below are for local dev or if
you ever turn auto-update off.

- Local test: `python3 server.py`, then open `http://localhost:3000`.
- Manual update + restart on the mini:
  1. `git pull` (or check out the branch you want to run).
  2. Double-click **`Restart Dashboard.command`** (or run it). It kickstarts the
     LaunchAgent if installed, otherwise stops/relaunches `server.py`.
  3. Hard-refresh the browser (⌘⇧R).
- **First-time setup — run `Install Dashboard Autostart.command` once.** It installs
  *both* LaunchAgents: the server (starts at login, relaunches on crash — the mac
  equivalent of the old Pi systemd unit) **and** the auto-updater (polls GitHub every
  2 min, pulls + restarts on new commits). After this, deploys are hands-off.
  - The auto-updater fast-forwards only. If the mini's checkout has local edits that
    can't fast-forward, it logs a warning to `auto-update.log` and leaves the files
    alone rather than clobbering them — resolve by hand and it resumes next tick.
  - The mini follows whatever branch is checked out (normally `main`), so merge your
    work to `main` for it to pick up automatically.

## Notes
- Git history lives in this repo on the Mac; the mini runs from a checkout of it.
- `history.db` is included in the Pi Forge credentials backup, so chart history (and the
  fun-card streak/first-seen in `app_state`) survives a rebuild.
