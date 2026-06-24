# BitAxe Dashboard

Web dashboard for monitoring BitAxe miners (and Pi temp, BTC price, history charts).
Runs on a Raspberry Pi 5 and is viewed locally, over Tailscale, or publicly via Cloudflare.

> One of several separate projects (see `~/dev/PROJECTS.md` for the full map). Related:
> **Pi Forge** (Mac recovery app, `~/dev/PiForge`) and the **BitAxe apps** "Prospector"
> (`~/dev/BitAxeApp`, which now includes the widgets — the old standalone widget repo is
> archived). Keep changes here scoped to the dashboard.

## What's here
- `server.py` — Python `http.server` on **port 3000**. Serves `index.html` and proxies
  miner APIs. Endpoints: `/api/system/info`, `/api/miners`, `/api/history`, `/api/btc-price`,
  `/api/pi-temp`, `/api/tickets`, `/api/energy`, `/api/miner/<n>/...`.
- `index.html` — the whole UI (single file, ~70 KB). Multi-miner: per-miner cards,
  combined summary, core-voltage card, Identify button.
- `config.json` — miner list (gitignored). Shape:
  `{"miners":[{"ip":"http://192.168.4.154","name":"BitAxe 1"},{"ip":"http://192.168.4.159","name":"BitAxe 2"}]}`
- `history.db` — SQLite chart history (gitignored; lives only on the Pi).
- `recover/` — older Terminal recovery toolkit (now superseded by the Pi Forge app).
- `Deploy to Pi.command`, `Start Dashboard.command`, `bitaxe-dashboard.service`.

## Miners & hosts
- BitAxe 1: `http://192.168.4.154`  •  BitAxe 2: `http://192.168.4.159`
- Pi: user `codydoerfler`, `raspberrypi.local` = `192.168.4.152` (LAN), `100.94.9.23` (Tailscale)
- Public URL: `https://bitaxe.rrwestminster.com` (Cloudflare tunnel → Pi nginx :80 → :3000)

## Run / deploy
- Local test: `python3 server.py` then open `http://localhost:3000`.
- Deploy to Pi: `scp index.html server.py codydoerfler@raspberrypi.local:~/bitaxe/`
  then `ssh codydoerfler@raspberrypi.local 'sudo systemctl restart bitaxe-dashboard'`.
  (Pi Forge's "Rebuild Stack" also deploys these.)

## Notes
- The Pi copy is a plain file copy, not a git checkout — git history lives only here on the Mac.
- `history.db` is now included in the Pi Forge credentials backup, so chart history survives a re-flash.
