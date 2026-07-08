#!/bin/bash
# TarsierPOS — idempotent installer for the kiosk self-heal (launcher + blank
# watchdog). Mirrors install-watchdog.sh: everything version-controlled in the
# repo, nothing hand-installed (the FLAG-072 netguard lesson).
#
# Installs:
#   /usr/local/bin/kiosk-run.sh                            (root-owned launcher)
#   /usr/local/sbin/tarsier-kiosk-watchdog                 (root-owned watchdog)
#   /etc/systemd/system/tarsier-kiosk-watchdog.service|.timer
#   ImageMagick (apt) if missing — the watchdog needs `import` + `convert`
# Enables the watchdog timer.
#
# Does NOT edit the kiosk user's ~/.xinitrc — that's user-specific; the final
# step is printed for you to apply by hand (replace the single chromium line
# with `exec /usr/local/bin/kiosk-run.sh`).
#
# Usage:  sudo bash scripts/install-kiosk-selfheal.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo (installs to /usr/local and /etc/systemd/system)." >&2
  exit 1
fi

# Repo root: this script lives in scripts/, so root is one level up.
TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/.." && pwd)}
SRC_KIOSK="$TARSIERPOS_DIR/scripts/kiosk"
SRC_UNITS="$TARSIERPOS_DIR/scripts/systemd"
DST_UNITS="/etc/systemd/system"
echo "TARSIERPOS_DIR=$TARSIERPOS_DIR"

# 1) Dependency: ImageMagick (import + convert) for the blank-screen detector.
if ! command -v convert >/dev/null 2>&1 || ! command -v import >/dev/null 2>&1; then
  echo "installing imagemagick (blank-screen detector dependency)..."
  apt-get update -qq && apt-get install -y imagemagick
else
  echo "  imagemagick already present"
fi

# 2) Root-owned script copies — units/.xinitrc must never execute the
#    user-writable repo path as root (netguard precedent).
install -m 0755 -o root -g root "$SRC_KIOSK/kiosk-run.sh"               /usr/local/bin/kiosk-run.sh
echo "  installed: /usr/local/bin/kiosk-run.sh"
install -m 0755 -o root -g root "$SRC_KIOSK/tarsier-kiosk-watchdog.sh"  /usr/local/sbin/tarsier-kiosk-watchdog
echo "  installed: /usr/local/sbin/tarsier-kiosk-watchdog"

# 3) systemd units + enable the watchdog timer.
install -m 0644 "$SRC_UNITS/tarsier-kiosk-watchdog.service" "$DST_UNITS/"
install -m 0644 "$SRC_UNITS/tarsier-kiosk-watchdog.timer"   "$DST_UNITS/"
echo "  installed: $DST_UNITS/tarsier-kiosk-watchdog.{service,timer}"
systemctl daemon-reload
systemctl enable --now tarsier-kiosk-watchdog.timer
echo "  enabled:   tarsier-kiosk-watchdog.timer (every 15s)"

echo
echo "DONE. One manual step remains (kiosk launcher):"
echo "  Edit the autologin kiosk user's ~/.xinitrc — keep the xrandr/setxkbmap/"
echo "  unclutter lines, and replace the single 'chromium --kiosk ... https://localhost'"
echo "  line with:"
echo "        exec /usr/local/bin/kiosk-run.sh"
echo "  Then restart the kiosk session (or reboot) to pick up software rendering."
echo
echo "Verify:"
echo "  - chrome://gpu shows software rendering"
echo "  - tail -f /var/log/tarsier/kiosk-watchdog.log   -> 'OK stddev=...' heartbeats"
echo "  - if XAUTHORITY differs from /home/posadmin/.Xauthority, edit"
echo "    $DST_UNITS/tarsier-kiosk-watchdog.service then: systemctl daemon-reload"
