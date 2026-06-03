# BitAxe Pi — Recovery Toolkit

The Pi's SD card corrupts on abrupt power loss. This turns recovery from an
hours-long ordeal into **two double-clicks**.

## The 2-step recovery

1. **Put the SD card in your Mac**, then double-click **`flash-card.command`**
   - Pick the SD card disk, type `ERASE` to confirm
   - Enter your Mac password (for the flash) and WiFi password (typed locally, never stored)
   - ~3–4 min. Then put the card in the Pi and power on.

2. After the Pi boots and joins WiFi (~4 min), double-click **`rebuild-stack.command`**
   - Finds the Pi (local or Tailscale), reinstalls the whole stack, and **restores
     saved credentials** so there's **no browser re-auth and no printer re-pairing**.
   - Verifies everything at the end.

That's it. Local dashboard, public `bitaxe.rrwestminster.com`, Tailscale, and both
OctoEverywhere printers all come back automatically.

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
