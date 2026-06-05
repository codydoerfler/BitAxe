# BitAxe Pi — Recovery Toolkit

The Pi corrupts its boot drive on abrupt power loss. This turns recovery from an
hours-long ordeal into **two double-clicks** — or one click in the app.

## Easiest: the app

Double-click **`BitAxe Recovery.app`** for a menu of everything:

1. **Flash a card or SSD** — write Pi OS + config to a blank SD card or USB SSD
2. **Rebuild the stack** — reinstall everything on the Pi + restore credentials
3. **Refresh credential backup** — re-save tunnel / Tailscale / printer config
4. **Safe shutdown the Pi** — graceful, UPS-friendly remote power-down

> First launch only: macOS asks permission for the app to control Terminal — click **OK**.

## The 2-step recovery (what options 1 + 2 do)

1. **Plug the target drive into your Mac** (SD card *or* USB SSD), then run
   **Flash a card or SSD** (`flash-card.command`)
   - Pick the disk, type `ERASE` to confirm
   - Enter your Mac password (for the flash) and WiFi password (typed locally, never stored)
   - ~3–4 min. Then put the drive in the Pi and power on.

2. After the Pi boots and joins WiFi (~4 min), run **Rebuild the stack**
   (`rebuild-stack.command`)
   - Finds the Pi (local or Tailscale), reinstalls the whole stack, and **restores
     saved credentials** so there's **no browser re-auth and no printer re-pairing**.
   - Verifies everything at the end.

That's it. Local dashboard, public `bitaxe.rrwestminster.com`, Tailscale, and both
OctoEverywhere printers all come back automatically.

## Booting from a USB SSD (recommended — barely corrupts)

An SSD survives power loss far better than an SD card. To migrate:

1. Flash the **SSD** with option 1 (your current SD card is never touched — it stays
   as an instant fallback).
2. Shut down the Pi, **remove the SD card**, plug the SSD into a **blue USB-3 port**.
3. Power on — the Pi 5 boots from USB automatically when no SD card is inserted.
4. Run option 2 to rebuild + verify.
5. **Undo, if ever needed:** power off, re-insert the SD card → you're back to the old
   setup (the Pi prefers the SD card when one is present).

## What's where

| Item | Location | In git? |
|---|---|---|
| Dashboard code (`index.html`, `server.py`) | `/Users/codydoerfler/BitAxe` | ✅ yes |
| These scripts | `/Users/codydoerfler/BitAxe/recover` | ✅ yes (no secrets) |
| Credentials backup (cloudflared, tailscale, octoeverywhere) | `~/bitaxe-pi-backup/pi-creds.tgz` | ❌ never |
| Printer LAN codes | `~/bitaxe-pi-backup/printers.env` | ❌ never |
| Cached OS image | `~/bitaxe-pi-backup/raspios.img.xz` | ❌ never |

## Keep the credential backup fresh

If you ever change the Cloudflare tunnel, Tailscale, or re-pair a printer, refresh
the backup so recovery stays current:
```bash
ssh codydoerfler@100.94.9.23 'sudo tar czf /tmp/pi-creds.tgz -C / etc/cloudflared home/codydoerfler/.cloudflared var/lib/tailscale/tailscaled.state -C /home/codydoerfler octoeverywhere-data 2>/dev/null; sudo chown codydoerfler /tmp/pi-creds.tgz'
scp codydoerfler@100.94.9.23:/tmp/pi-creds.tgz ~/bitaxe-pi-backup/ && ssh codydoerfler@100.94.9.23 'rm /tmp/pi-creds.tgz'
```

## The real fix (stop the corruption)

Recovery is fast now, but SD cards corrupt on power loss because they're fragile.
Best prevention, in order:
1. **Boot the Pi 5 from a USB SSD** instead of the SD card — SSDs barely corrupt. (~$30, requires re-imaging onto the SSD.)
2. **Small UPS** — clean power means no corruption. (~$40.)

> ⚠️ Do **not** enable the raspi-config read-only "overlay filesystem" — it breaks
> Docker/OctoEverywhere persistence and resets `history.db` every reboot.

## Reference

- Pi: `192.168.4.152` (local) / `100.94.9.23` (Tailscale), user `codydoerfler`, SSH key-only, NOPASSWD sudo
- This image provisions via **cloud-init NoCloud** (seed from `/boot/firmware`), NOT `custom.toml`
- Miner: `192.168.4.154` (AxeOS)
