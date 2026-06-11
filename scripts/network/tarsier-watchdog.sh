#!/bin/bash
# =============================================================================
# FEATURE-045: tarsier-watchdog — WiFi/Tailscale self-healing connectivity
# watchdog.
#
# The client box (pos-01) drops off Tailscale after boot and remote support
# goes blind. A systemd timer runs this script every 2 minutes:
#
#   check 1: ping the default gateway
#   check 2: tailscale BackendState == Running
#
# Both pass  -> reset the failure counter (logging the recovery if there was
#               an outage in progress) and exit.
# Any fails  -> increment a persistent failure counter. From the 3rd
#               consecutive failure onward, run ONE escalation step per cycle,
#               in order, cycling if the outage persists:
#                 1. nmcli connection up <active/autoconnect wifi profile>
#                 2. systemctl restart NetworkManager
#                 3. systemctl restart tailscaled
#               NEVER reboots the box.
#
# Every check result and every action is logged with a timestamp to
# /var/log/tarsier/connectivity.log (logrotate: scripts/logrotate/
# tarsier-connectivity, 14 days). Healthy cycles are NOT logged — the log is
# an evidence trail of outages, recoveries, and actions, not a heartbeat.
#
# Dev-box safe: if tailscale or nmcli is not installed the corresponding
# check/action is skipped (logged), never failed. State lives in /run so a
# reboot starts with a clean counter (boot connectivity is judged fresh).
#
# INSTALL (root-owned, NOT run from the user-writable repo path — same model
# as netguard): scripts/install-watchdog.sh does this. By hand:
#   sudo install -m 0755 -o root -g root scripts/network/tarsier-watchdog.sh \
#        /usr/local/sbin/tarsier-watchdog
#
# Test/dry-run hooks (never set in production):
#   TARSIER_WD_STATE_DIR   override /run/tarsier-watchdog
#   TARSIER_WD_LOG         override /var/log/tarsier/connectivity.log
#   TARSIER_WD_DRY_RUN=1   log intended actions, execute nothing
#   NMCLI / TAILSCALE / SYSTEMCTL / PING / IP   binary overrides for stubs
# =============================================================================
set -euo pipefail

NMCLI="${NMCLI:-nmcli}"
TAILSCALE="${TAILSCALE:-tailscale}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
PING="${PING:-ping}"
IP="${IP:-ip}"
DRY_RUN="${TARSIER_WD_DRY_RUN:-0}"

STATE_DIR="${TARSIER_WD_STATE_DIR:-/run/tarsier-watchdog}"
FAIL_FILE="$STATE_DIR/failcount"
LOG_FILE="${TARSIER_WD_LOG:-/var/log/tarsier/connectivity.log}"
FAIL_THRESHOLD=3

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"

ts()  { date '+%Y-%m-%d %H:%M:%S%z'; }
log() { echo "$(ts) [watchdog] $*" >>"$LOG_FILE" 2>/dev/null || true; }

run_action() {  # run_action <description> <cmd...>
  local desc="$1"; shift
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: would $desc"
    return 0
  fi
  log "ACTION: $desc"
  if "$@" >>"$LOG_FILE" 2>&1; then
    log "ACTION OK: $desc"
  else
    log "ACTION FAILED (rc=$?): $desc"
  fi
}

# --- check 1: default gateway ping -------------------------------------------
gateway_ok() {
  local gw
  gw="$($IP route show default 2>/dev/null | awk '/^default/ {print $3; exit}')"
  if [ -z "$gw" ]; then
    GW_DETAIL="no default route"
    return 1
  fi
  if "$PING" -c 1 -W 2 "$gw" >/dev/null 2>&1; then
    GW_DETAIL="gateway $gw reachable"
    return 0
  fi
  GW_DETAIL="gateway $gw unreachable"
  return 1
}

# --- check 2: tailscale backend running ---------------------------------------
tailscale_ok() {
  if ! command -v "$TAILSCALE" >/dev/null 2>&1; then
    TS_DETAIL="tailscale not installed — check skipped"
    return 0
  fi
  local state
  state="$("$TAILSCALE" status --json 2>/dev/null \
           | grep -o '"BackendState":[[:space:]]*"[^"]*"' \
           | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
  if [ "$state" = "Running" ]; then
    TS_DETAIL="tailscale BackendState=Running"
    return 0
  fi
  TS_DETAIL="tailscale BackendState=${state:-unknown}"
  return 1
}

# --- escalation steps ----------------------------------------------------------
escalate() {  # escalate <step 0|1|2>
  case "$1" in
    0)
      if ! command -v "$NMCLI" >/dev/null 2>&1; then
        log "ESCALATE 1/3 skipped: nmcli not installed"
        return 0
      fi
      local profile
      profile="$("$NMCLI" -t -f NAME connection show --active 2>/dev/null | head -1)"
      if [ -z "$profile" ]; then
        profile="$("$NMCLI" -t -f NAME,TYPE,AUTOCONNECT connection show 2>/dev/null \
                   | awk -F: '$2 ~ /wireless|wifi/ && $3 == "yes" {print $1; exit}')"
      fi
      if [ -z "$profile" ]; then
        log "ESCALATE 1/3 skipped: no active or autoconnect wifi profile found"
        return 0
      fi
      run_action "nmcli connection up '$profile' (escalation 1/3)" \
        "$NMCLI" connection up "$profile"
      ;;
    1)
      run_action "systemctl restart NetworkManager (escalation 2/3)" \
        "$SYSTEMCTL" restart NetworkManager
      ;;
    2)
      run_action "systemctl restart tailscaled (escalation 3/3)" \
        "$SYSTEMCTL" restart tailscaled
      ;;
  esac
}

# --- main ----------------------------------------------------------------------
GW_DETAIL=""; TS_DETAIL=""
gw_pass=0; ts_pass=0
gateway_ok && gw_pass=1
tailscale_ok && ts_pass=1

failcount=0
[ -f "$FAIL_FILE" ] && failcount="$(cat "$FAIL_FILE" 2>/dev/null || echo 0)"
case "$failcount" in (*[!0-9]*|'') failcount=0;; esac

if [ "$gw_pass" = "1" ] && [ "$ts_pass" = "1" ]; then
  if [ "$failcount" -gt 0 ]; then
    log "RECOVERED after $failcount failed check(s): $GW_DETAIL; $TS_DETAIL"
  fi
  echo 0 > "$FAIL_FILE"
  exit 0
fi

failcount=$((failcount + 1))
echo "$failcount" > "$FAIL_FILE"
log "CHECK FAILED ($failcount consecutive): $GW_DETAIL; $TS_DETAIL"

if [ "$failcount" -lt "$FAIL_THRESHOLD" ]; then
  exit 0
fi

step=$(( (failcount - FAIL_THRESHOLD) % 3 ))
log "STATE at escalation: failcount=$failcount gateway_ok=$gw_pass tailscale_ok=$ts_pass"
escalate "$step"
exit 0
