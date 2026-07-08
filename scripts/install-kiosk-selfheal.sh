#!/bin/bash
# TarsierPOS — idempotent installer for the kiosk self-heal (launcher + blank
# watchdog). Mirrors install-watchdog.sh: everything version-controlled in the
# repo, nothing hand-installed (the FLAG-072 netguard lesson).
#
# Installs:
#   /usr/local/bin/kiosk-run.sh                            (root-owned launcher)
#   /usr/local/sbin/tarsier-kiosk-watchdog                 (root-owned watchdog)
#   /etc/systemd/system/tarsier-kiosk-watchdog.service|.timer  + enables the timer
#   ImageMagick (best-effort; the watchdog needs `import`+`convert`, the
#   launcher does not — so a failed apt never blocks the core self-heal)
#
# Does NOT edit the kiosk user's ~/.xinitrc — that's user-specific and a wrong
# edit can black-screen the kiosk. Apply that by hand AFTER verifying the
# launcher exists (this script prints the exact step + a self-protecting form).
#
# Usage:  sudo bash scripts/install-kiosk-selfheal.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo (installs to /usr/local and /etc/systemd/system)." >&2
  exit 1
fi

TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/.." && pwd)}
SRC_KIOSK="$TARSIERPOS_DIR/scripts/kiosk"
SRC_UNITS="$TARSIERPOS_DIR/scripts/systemd"
DST_UNITS="/etc/systemd/system"
echo "TARSIERPOS_DIR=$TARSIERPOS_DIR"

# 1) CRITICAL FIRST — root-owned script copies. The launcher is what the kiosk
#    boot depends on, so it must be installed even if the (optional) ImageMagick
#    step later fails. Units must never execute the user-writable repo path as
#    root (netguard precedent).
install -m 0755 -o root -g root "$SRC_KIOSK/kiosk-run.sh"               /usr/local/bin/kiosk-run.sh
echo "  installed: /usr/local/bin/kiosk-run.sh"
install -m 0755 -o root -g root "$SRC_KIOSK/tarsier-kiosk-watchdog.sh"  /usr/local/sbin/tarsier-kiosk-watchdog
echo "  installed: /usr/local/sbin/tarsier-kiosk-watchdog"

# 2) systemd units + enable the watchdog timer.
install -m 0644 "$SRC_UNITS/tarsier-kiosk-watchdog.service" "$DST_UNITS/"
install -m 0644 "$SRC_UNITS/tarsier-kiosk-watchdog.timer"   "$DST_UNITS/"
echo "  installed: $DST_UNITS/tarsier-kiosk-watchdog.{service,timer}"
systemctl daemon-reload
systemctl enable --now tarsier-kiosk-watchdog.timer
echo "  enabled:   tarsier-kiosk-watchdog.timer (every 15s)"

# 3) BEST-EFFORT — ImageMagick for the blank-screen detector. NON-FATAL: the
#    launcher (software render + relaunch loop) works without it; only the
#    watchdog's blank detection needs it (it no-ops safely until present).
if command -v convert >/dev/null 2>&1 && command -v import >/dev/null 2>&1; then
  echo "  imagemagick already present"
else
  echo "installing imagemagick (blank-screen detector; non-fatal)..."
  if apt-get update -qq && apt-get install -y imagemagick; then
    echo "  imagemagick installed"
  else
    echo "  WARNING: imagemagick install failed — launcher self-heal is fine; the"
    echo "  blank-screen watchdog will no-op until you run:"
    echo "      sudo apt-get install -y imagemagick"
  fi
fi

# 4) Verify the launcher is actually in place before anyone points .xinitrc at it.
if [ -x /usr/local/bin/kiosk-run.sh ]; then
  echo "  VERIFIED: /usr/local/bin/kiosk-run.sh is installed and executable"
else
  echo "ERROR: /usr/local/bin/kiosk-run.sh is missing — do NOT edit .xinitrc yet." >&2
  exit 1
fi

cat <<'EOF'

DONE (systemd side). One manual step remains — point the kiosk launch at it.
Back up first, then in the autologin kiosk user's ~/.xinitrc replace the single
`chromium --kiosk ... https://localhost` line with a SELF-PROTECTING form that
falls back to the original launch if the new one ever goes missing:

    if [ -x /usr/local/bin/kiosk-run.sh ]; then
      exec /usr/local/bin/kiosk-run.sh
    else
      exec chromium --kiosk --incognito https://localhost   # <-- your ORIGINAL line
    fi

Keep the xrandr / setxkbmap / unclutter setup ABOVE it as-is. Then reboot.

Verify after reboot:
  - chrome://gpu shows software rendering
  - tail -f /var/log/tarsier/kiosk-watchdog.log  -> "OK stddev=..." heartbeats
EOF
