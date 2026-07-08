#!/bin/bash
# =============================================================================
# tarsier-kiosk-watchdog — self-heal a BLANK / dead kiosk screen.
#
# Companion to kiosk-run.sh. The launcher already relaunches Chromium if the
# process dies; this covers the residual case the launcher can't see: the
# browser process is ALIVE but the tab has gone to a uniform white (or black)
# screen — a renderer crash / dead compositor. Today that state needs a manual
# reboot (the FEATURE-045 watchdog only heals the network). This one detects it
# and recovers automatically, NEVER rebooting.
#
# HOW: every cycle it screenshots the root window, downscales it, and reads the
# image's normalized standard deviation. A live UI always has structure, so its
# stddev is high; a dead uniform screen is ~0. Validated on real captures:
#   pure white/black crash screen -> stddev 0.0000
#   healthy POS screens           -> stddev 0.13 .. 0.29
# so BLANK_STDDEV=0.012 sits ~10x below the lightest healthy screen: a normal
# (even mostly-white) POS never trips it.
#
# Only after BLANK_THRESHOLD consecutive blank cycles (default 3 -> ~30-45s of
# continuous dead screen) does it RECOVER by killing Chromium; kiosk-run.sh then
# relaunches a fresh POS in ~2s. One escalation per persistent outage, and it
# NEVER reboots.
#
# DEV / DEGRADE SAFE: with no DISPLAY, no screenshot tool (import/scrot), or a
# failed capture, it logs the reason and takes NO action (never a false kill).
#
# INSTALL: root-owned copy + a systemd timer (see scripts/kiosk/README.md).
# Needs ImageMagick (`import`+`convert`) or scrot on the box, and access to the
# kiosk X display (DISPLAY / XAUTHORITY below).
#
# Tunables (env): KIOSK_WD_DISPLAY, KIOSK_WD_XAUTHORITY, BLANK_STDDEV,
# BLANK_THRESHOLD, KIOSK_WD_LOG, KIOSK_WD_STATE_DIR, CHROME_PATTERN,
# KIOSK_WD_DRY_RUN=1 (decide + log, never kill).
# =============================================================================
set -uo pipefail

DISPLAY_ID="${KIOSK_WD_DISPLAY:-:0}"
XAUTH="${KIOSK_WD_XAUTHORITY:-}"
BLANK_STDDEV="${BLANK_STDDEV:-0.012}"
BLANK_THRESHOLD="${BLANK_THRESHOLD:-3}"
CHROME_PATTERN="${CHROME_PATTERN:-chrom}"     # matches chromium / chrome
DRY_RUN="${KIOSK_WD_DRY_RUN:-0}"

STATE_DIR="${KIOSK_WD_STATE_DIR:-/run/tarsier-kiosk-watchdog}"
FAIL_FILE="$STATE_DIR/blankcount"
LOG_FILE="${KIOSK_WD_LOG:-/var/log/tarsier/kiosk-watchdog.log}"
SHOT="${STATE_DIR}/shot.png"

mkdir -p "$STATE_DIR" 2>/dev/null || true
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') kiosk-wd: $*" >> "$LOG_FILE" 2>/dev/null; }

read_count(){ cat "$FAIL_FILE" 2>/dev/null || echo 0; }
write_count(){ echo "$1" > "$FAIL_FILE" 2>/dev/null || true; }

export DISPLAY="$DISPLAY_ID"
[ -n "$XAUTH" ] && export XAUTHORITY="$XAUTH"

# --- capture the root window to $SHOT; echo "ok" or a skip-reason ------------
capture(){
  if command -v import >/dev/null 2>&1; then
    import -silent -window root -resize 160x "$SHOT" >/dev/null 2>&1 && { echo ok; return; }
    echo "import-failed"; return
  fi
  if command -v scrot >/dev/null 2>&1; then
    scrot -o "$SHOT" >/dev/null 2>&1 && { echo ok; return; }
    echo "scrot-failed"; return
  fi
  echo "no-screenshot-tool"
}

# --- normalized stddev of $SHOT via ImageMagick; empty on failure -----------
stddev_of(){
  command -v convert >/dev/null 2>&1 || { echo ""; return; }
  convert "$SHOT" -colorspace Gray -format '%[fx:standard_deviation]' info: 2>/dev/null
}

cap="$(capture)"
if [ "$cap" != "ok" ]; then
  # Can't see the screen -> not our failure to fix. Log heartbeat, no action.
  log "SKIP blank-check ($cap) display=$DISPLAY"
  exit 0
fi

sd="$(stddev_of)"
if [ -z "$sd" ]; then
  log "SKIP blank-check (stddev-read-failed; is ImageMagick 'convert' installed?)"
  exit 0
fi

# blank if stddev below threshold (uniform screen)
is_blank=$(awk -v s="$sd" -v t="$BLANK_STDDEV" 'BEGIN{print (s+0 < t+0) ? 1 : 0}')
count=$(read_count)

if [ "$is_blank" -eq 0 ]; then
  if [ "$count" -ne 0 ]; then log "RECOVERED screen not blank (stddev=$sd) after $count blank cycle(s)"; fi
  write_count 0
  log "OK stddev=$sd blank=0"
  exit 0
fi

count=$((count + 1))
write_count "$count"
log "CHECK FAILED screen blank (stddev=$sd) consecutive=$count/$BLANK_THRESHOLD"

if [ "$count" -lt "$BLANK_THRESHOLD" ]; then
  exit 0
fi

# Threshold reached -> recover: kill Chromium so kiosk-run.sh relaunches it.
if [ "$DRY_RUN" = "1" ]; then
  log "DRY_RUN would recover: pkill -f '$CHROME_PATTERN' (blank $count cycles)"
  exit 0
fi
if pkill -f "$CHROME_PATTERN" 2>/dev/null; then
  log "RECOVER killed Chromium after $count blank cycles — launcher will relaunch"
else
  log "RECOVER no Chromium process matched '$CHROME_PATTERN' (launcher may already be relaunching)"
fi
write_count 0
exit 0
