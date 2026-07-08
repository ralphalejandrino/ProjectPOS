#!/bin/bash
# =============================================================================
# TarsierPOS kiosk launcher — crash-resistant + self-healing.
#
# WHY: the PROD kiosk (low-end mini-PC + ANMITE panel) went to a FULL WHITE
# SCREEN during cash entry and needed a manual REBOOT. The app-side trigger is
# fixed, but a cheap GPU can still crash the browser compositor and nothing on
# the box recovered a blanked browser. This launcher gives two guarantees:
#
#   1) REMOVE THE MOST LIKELY CAUSE — software rendering (--disable-gpu). A POS
#      web UI needs no GPU; taking it out of the path eliminates the compositor-
#      crash class that produces the white screen on flaky panels/drivers.
#   2) SELF-HEAL — relaunch loop. Any Chromium exit relaunches a fresh POS in
#      ~2s. The screen comes back on its own, no operator, no reboot.
#
# The flag set MIRRORS the proven pos-01 launch — most importantly
# --ignore-certificate-errors (the POS is https://localhost behind a self-signed
# / Tailscale cert; without this flag the kiosk lands on a cert-warning page
# instead of the POS) and the snap chromium path.
#
# INSTALL (NEW box): point the kiosk X startup (~/.xinitrc for the autologin
# kiosk user) at this, keeping the display setup (xrandr/setxkbmap/unclutter)
# above it:
#       exec /usr/local/bin/kiosk-run.sh
#
# EXISTING box that already has its own `while true; do chromium ...; done` loop
# in .xinitrc (like pos-01): you do NOT need to swap in this script — just
# add `--disable-gpu` to that existing chromium line and install the watchdog.
# That keeps every box-specific flag you already rely on.
#
# Tunables (env): KIOSK_URL (default https://localhost), KIOSK_LOG, CHROME_BIN.
# =============================================================================
set -u

KIOSK_URL="${KIOSK_URL:-https://localhost}"
LOG="${KIOSK_LOG:-/var/log/tarsier/kiosk.log}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') kiosk: $*" >> "$LOG" 2>/dev/null; }

# Prefer the snap chromium path used on the client boxes, then PATH lookups.
CHROME_BIN="${CHROME_BIN:-}"
if [ -z "$CHROME_BIN" ]; then
  for c in /snap/bin/chromium chromium chromium-browser google-chrome google-chrome-stable; do
    if [ -x "$c" ] || command -v "$c" >/dev/null 2>&1; then CHROME_BIN="$c"; break; fi
  done
fi
[ -n "$CHROME_BIN" ] || CHROME_BIN=/snap/bin/chromium

FLAGS=(
  --kiosk
  --disable-gpu                             # software rendering: no GPU compositor to crash
  --noerrdialogs
  --disable-infobars
  --no-first-run
  --disable-session-crashed-bubble
  --ignore-certificate-errors               # POS is https://localhost w/ self-signed/Tailscale cert
  --incognito                               # clean login each launch
  --disable-features=PWAInstallPrompt,WebAppInstallation
  --check-for-update-interval=31536000
)

log "launcher starting (url=$KIOSK_URL, bin=$CHROME_BIN, software-render)"
trap 'log "launcher received TERM/INT — exiting loop"; exit 0' TERM INT

while true; do
  log "launching chromium"
  "$CHROME_BIN" "${FLAGS[@]}" "$KIOSK_URL" >> "$LOG" 2>&1
  log "chromium exited (code=$?) — relaunching in 2s"
  sleep 2
done
