#!/bin/bash
# =============================================================================
# TarsierPOS kiosk launcher — crash-resistant + self-healing.
#
# WHY: the PROD kiosk (low-end mini-PC + ANMITE panel) went to a FULL WHITE
# SCREEN during cash entry and needed a manual REBOOT to recover. The app-side
# trigger (on-screen-keyboard input swallowing taps) is fixed, but a cheap GPU
# can still crash the browser compositor for other reasons, and today NOTHING
# on the box recovers a blanked browser (the FEATURE-045 watchdog only heals
# WiFi/Tailscale). This launcher closes that gap with two guarantees:
#
#   1) REMOVE THE MOST LIKELY CAUSE — software rendering.
#      Chromium runs with --disable-gpu, so there is no GPU compositor to
#      crash. A point-of-sale web UI needs no hardware acceleration; taking the
#      GPU out of the path eliminates the whole compositor-crash class that
#      produces the white screen on flaky panels/drivers.
#
#   2) SELF-HEAL — relaunch loop.
#      Chromium runs inside `while true`. If it EVER exits (crash, OOM, or a
#      kill from a watchdog), it relaunches in ~2s straight back onto the POS
#      login. The screen comes back on its own — no operator, no reboot.
#
# INSTALL (on the client box): replace the single Chromium line in the kiosk
# X startup (~/.xinitrc for the autologin kiosk user) with:
#
#       exec /usr/local/bin/kiosk-run.sh
#
# Keep the display setup ABOVE it (xrandr primary-output, setxkbmap, unclutter)
# exactly as-is. Install the script root-owned, like netguard/watchdog:
#       sudo install -m 0755 -o root -g root \
#            scripts/kiosk/kiosk-run.sh /usr/local/bin/kiosk-run.sh
#
# TUNABLES (env): KIOSK_URL (default https://localhost), KIOSK_LOG, CHROME_BIN.
#
# NOTE (deploy verification): confirm the exact Chromium binary name on the box
# (snap ships `chromium`) and that --disable-gpu is honoured (chrome://gpu should
# show software rendering). Software rendering trades a little CPU for stability;
# on the M710q-class CPU a POS UI stays smooth.
# =============================================================================
set -u

KIOSK_URL="${KIOSK_URL:-https://localhost}"
LOG="${KIOSK_LOG:-/var/log/tarsier/kiosk.log}"
PROFILE_DIR="${KIOSK_PROFILE:-/tmp/tarsier-kiosk-profile}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') kiosk: $*" >> "$LOG" 2>/dev/null; }

# Resolve a Chromium binary (snap wrapper is just `chromium`).
CHROME_BIN="${CHROME_BIN:-$(command -v chromium || command -v chromium-browser \
  || command -v google-chrome || command -v google-chrome-stable || echo chromium)}"

FLAGS=(
  --kiosk
  --incognito                               # clean login each launch
  --disable-gpu                             # software rendering: no GPU compositor to crash
  --disable-gpu-compositing
  --noerrdialogs                            # no crash/error dialogs over the POS
  --disable-session-crashed-bubble
  --disable-infobars
  --disable-features=Translate,TranslateUI
  --no-first-run
  --check-for-update-interval=31536000
  --overscroll-history-navigation=0
  --user-data-dir="$PROFILE_DIR"            # own profile dir so a stale lock never blocks relaunch
)

log "launcher starting (url=$KIOSK_URL, bin=$CHROME_BIN, software-render)"
trap 'log "launcher received TERM/INT — exiting loop"; exit 0' TERM INT

while true; do
  # Clear any Singleton lock left by a crashed prior instance so the relaunch
  # never fails with "profile appears to be in use".
  rm -f "$PROFILE_DIR"/Singleton* 2>/dev/null || true
  log "launching chromium"
  "$CHROME_BIN" "${FLAGS[@]}" "$KIOSK_URL" >> "$LOG" 2>&1
  log "chromium exited (code=$?) — relaunching in 2s"
  sleep 2
done
