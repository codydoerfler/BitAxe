#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# flash-card.command — Step 1 of Pi recovery
# Flashes Raspberry Pi OS to an SD card OR a USB SSD and writes the cloud-init
# headless config (the format THIS image actually wants — discovered the hard way).
# A USB SSD is the more durable choice; the Pi 5 boots it from a blue USB-3 port
# when no SD card is inserted.
# Double-click to run. You'll be asked for your Mac password (for the flash)
# and your WiFi password (typed locally, never stored).
# ─────────────────────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

SSH_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPaBAhl9scM8aIfVf4yNkG8ZwmN+aily6MIyIYB7O2er codydoerfler@Codys-MacBook-Air.local"
WIFI_SSID="Doerfler"
WIFI_COUNTRY="US"
IMG_CACHE="$HOME/bitaxe-pi-backup/raspios.img.xz"
HOMEBREW_XZ="/opt/homebrew/bin/xz"

echo "════════════════════════════════════════════════════════"
echo "  BitAxe Pi — Flash & Provision (SD card or USB SSD)"
echo "════════════════════════════════════════════════════════"

# 1. Identify the target drive — SD card OR USB SSD (external, physical)
echo; echo "→ Looking for external drives (SD cards and USB SSDs)..."
diskutil list external physical
echo
read -p "Enter the disk identifier of the SD card / USB SSD (e.g. disk8) — LOOK CAREFULLY, this ERASES it: " DISK
[ -z "$DISK" ] && { echo "No disk entered. Aborting."; exit 1; }
# sanity: must be a USB or SD (external) disk — refuses the Mac's internal NVMe/SATA.
# USB SSDs report Protocol:USB and Removable Media:Fixed (not "Ejectable:Yes"),
# so we check Protocol instead of Ejectable.
DINFO=$(diskutil info "$DISK" 2>/dev/null)
PROTO=$(echo "$DINFO" | awk -F: '/Protocol/{gsub(/ /,"",$2); print $2}')
if [[ "$PROTO" != "USB" && "$PROTO" != "SD" ]]; then
  echo "✗ $DISK does not appear to be a USB or SD drive (Protocol: $PROTO). Aborting for safety."; exit 1
fi
SIZE=$(echo "$DINFO" | grep "Disk Size" | head -1)
echo "→ Target: /dev/$DISK  ($SIZE)"
read -p "Type ERASE to confirm wiping /dev/$DISK: " CONFIRM
[ "$CONFIRM" = "ERASE" ] || { echo "Not confirmed. Aborting."; exit 1; }

# 2. Get the OS image (cached or download latest)
if [ ! -f "$IMG_CACHE" ]; then
  echo "→ Downloading Raspberry Pi OS Lite 64-bit (~550MB)..."
  mkdir -p "$(dirname "$IMG_CACHE")"
  curl -L -o "$IMG_CACHE" "https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
fi
echo "→ Verifying image..."; "$HOMEBREW_XZ" -t "$IMG_CACHE"

# 3. Flash it
echo "→ Unmounting and flashing (you'll be asked for your Mac password)..."
diskutil unmountDisk "/dev/$DISK"
"$HOMEBREW_XZ" -dc "$IMG_CACHE" | sudo dd of="/dev/r$DISK" bs=4m
sync

# 4. Write cloud-init headless config to the boot partition
echo "→ Writing cloud-init config..."
sleep 2
diskutil mount "${DISK}s1" >/dev/null 2>&1 || diskutil mount "$(diskutil list "$DISK" | awk '/Windows_FAT_32/{print $NF}')" >/dev/null 2>&1
B="/Volumes/bootfs"
[ -d "$B" ] || { echo "✗ boot partition didn't mount. Check Finder for 'bootfs'."; exit 1; }

# WiFi password — typed locally, never stored
echo
read -s -p "Enter WiFi password for '$WIFI_SSID' (hidden): " WIFI_PW; echo

cat > "$B/meta-data" <<EOF
instance-id: bitaxe-pi-$(date +%s)
local-hostname: raspberrypi
EOF

cat > "$B/user-data" <<EOF
#cloud-config
hostname: raspberrypi
manage_etc_hosts: true
timezone: America/Denver
users:
  - name: codydoerfler
    groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo
    shell: /bin/bash
    lock_passwd: true
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - $SSH_PUBKEY
ssh_pwauth: false
runcmd:
  - systemctl enable --now ssh
EOF

cat > "$B/network-config" <<EOF
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
  wifis:
    wlan0:
      dhcp4: true
      regulatory-domain: "$WIFI_COUNTRY"
      access-points:
        "$WIFI_SSID":
          password: "$WIFI_PW"
      optional: true
EOF
touch "$B/ssh"
unset WIFI_PW
dot_clean "$B" 2>/dev/null; rm -f "$B/._"* 2>/dev/null || true

sync; diskutil eject "/dev/$DISK"
echo
echo "✓ DONE. Put the card in the Pi, power on, wait ~4 min for it to join WiFi."
echo "  Then double-click  rebuild-stack.command  to restore everything."
read -p "Press Enter to close."
