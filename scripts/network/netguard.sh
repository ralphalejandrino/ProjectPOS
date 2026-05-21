#!/bin/bash
# =============================================================================
# FEATURE-039: tarsier-netguard — bulletproof WiFi change guard.
#
# The target box (pos-01) is headless and reachable ONLY via Tailscale over
# WiFi. A bad WiFi change severs SSH + Tailscale at once and there is no onsite
# keyboard. This script makes WiFi changes self-healing:
#
#   apply   create a NEW nm profile (never touch the working one), record the
#           previous working profile, arm a 60s revert timer, then activate.
#   confirm explicit admin success signal — cancels the pending revert.
#   check   if a change is still unconfirmed, revert to the previous profile.
#           Run by BOTH the 60s timer AND the on-boot unit, so a power cut
#           mid-change cannot strand the box (the timer dies on reboot; the boot
#           unit catches it — any 'pending' state at boot means never-confirmed).
#   status  print state.json (read-only).
#
# INSTALL (root-owned, NOT run from the user-writable repo path):
#   sudo install -m 0755 -o root -g root scripts/network/netguard.sh \
#        /usr/local/sbin/tarsier-netguard
#
# Privileged entrypoints (apply/confirm) are exposed to the app user via a
# scoped sudoers rule — see scripts/sudoers/tarsierpos-network. 'check' is only
# ever invoked by root (systemd timer + boot unit), never by the app user.
#
# Test/dry-run hooks (never set in production):
#   NMCLI            override the nmcli binary (tests point this at a stub)
#   SYSTEMD_RUN      override systemd-run (tests set to 'true' = no-op)
#   TARSIER_NET_STATE_DIR  override /var/lib/tarsierpos-network
# =============================================================================
set -euo pipefail

NMCLI="${NMCLI:-nmcli}"
SYSTEMD_RUN="${SYSTEMD_RUN:-systemd-run}"
STATE_DIR="${TARSIER_NET_STATE_DIR:-/var/lib/tarsierpos-network}"
STATE_FILE="$STATE_DIR/state.json"
LOG_FILE="$STATE_DIR/netguard.log"
REVERT_WINDOW_SECONDS=60
SELF="$(readlink -f "$0")"

mkdir -p "$STATE_DIR"

ts()  { date -Iseconds; }
log() { echo "$(ts) [netguard] $*" >>"$LOG_FILE" 2>/dev/null || true; }

# --- state helpers (python3 for robust JSON; present on the target image) -----
state_get() {  # state_get <key> -> value or empty
  [ -f "$STATE_FILE" ] || { echo ""; return 0; }
  python3 - "$STATE_FILE" "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        print(json.load(f).get(sys.argv[2], "") or "")
except Exception:
    print("")
PY
}

state_write() {  # state_write key=val key=val ...  (atomic)
  local tmp; tmp="$(mktemp "$STATE_DIR/.state.XXXXXX")"
  python3 - "$tmp" "$@" <<'PY'
import json, sys
tmp = sys.argv[1]
d = {}
for kv in sys.argv[2:]:
    k, _, v = kv.partition("=")
    d[k] = v
with open(tmp, "w") as f:
    json.dump(d, f)
PY
  chmod 0644 "$tmp"
  mv -f "$tmp" "$STATE_FILE"   # atomic replace; survives power cut (no partial)
}

active_wifi_conn() {  # name of the currently active wifi connection (or empty)
  "$NMCLI" -t -f NAME,TYPE connection show --active 2>/dev/null \
    | awk -F: '$2=="802-11-wireless"{print $1; exit}'
}

wifi_device() {  # first wifi device name (or empty)
  "$NMCLI" -t -f DEVICE,TYPE device 2>/dev/null \
    | awk -F: '$2=="wifi"{print $1; exit}'
}

# --- revert: restore previous working profile, discard the failed new one -----
do_revert() {
  local prev new
  prev="$(state_get previous)"
  new="$(state_get new)"
  log "REVERT start previous='$prev' new='$new'"
  if [ -n "$new" ]; then
    "$NMCLI" connection down "$new"   >/dev/null 2>&1 || true
    "$NMCLI" connection delete "$new" >/dev/null 2>&1 || true
  fi
  if [ -n "$prev" ]; then
    "$NMCLI" connection up "$prev" >/dev/null 2>&1 || log "WARN nmcli up '$prev' failed"
  fi
  state_write status=reverted previous="$prev" new="$new" reverted_at="$(ts)"
  log "REVERT done"
}

cmd_apply() {  # cmd_apply <ssid>   (PSK read from stdin, kept off argv/ps)
  local ssid="${1:-}"
  [ -n "$ssid" ] || { echo "error: ssid required" >&2; exit 2; }
  local psk; psk="$(cat)"   # may be empty for open networks

  if [ "$(state_get status)" = "pending" ]; then
    echo "error: a network change is already pending confirmation" >&2
    exit 3
  fi

  local dev prev new
  dev="$(wifi_device)"
  [ -n "$dev" ] || { echo "error: no wifi device found" >&2; exit 4; }
  prev="$(active_wifi_conn)"
  new="tarsier-wifi-$(date +%s)"
  log "APPLY ssid='$ssid' dev='$dev' previous='$prev' new='$new'"

  # 1) build the NEW profile — the working profile is never modified.
  "$NMCLI" connection add type wifi ifname "$dev" con-name "$new" ssid "$ssid" \
    >/dev/null
  if [ -n "$psk" ]; then
    "$NMCLI" connection modify "$new" \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$psk" >/dev/null
  fi

  # 2) persist intent BEFORE activating, so a power cut during step 4 is covered.
  state_write status=pending previous="$prev" new="$new" \
    applied_at="$(ts)" deadline="$(date -Iseconds -d "+${REVERT_WINDOW_SECONDS} seconds")"

  # 3) arm the powered sad-path timer (boot unit is the power-cut guarantee).
  "$SYSTEMD_RUN" --on-active="${REVERT_WINDOW_SECONDS}" \
    --unit=tarsier-netrevert --collect "$SELF" check >/dev/null 2>&1 \
    || log "WARN could not arm revert timer (boot unit still covers this)"

  # 4) activate — THIS may sever SSH + Tailscale. Done last, on purpose.
  if ! "$NMCLI" connection up "$new" >/dev/null 2>&1; then
    log "APPLY activation failed — reverting immediately"
    do_revert
    echo "error: could not activate new network; reverted" >&2
    exit 5
  fi
  log "APPLY active, awaiting confirm within ${REVERT_WINDOW_SECONDS}s"
  echo "pending"
}

cmd_confirm() {
  if [ "$(state_get status)" != "pending" ]; then
    echo "error: nothing pending to confirm" >&2
    exit 6
  fi
  systemctl stop tarsier-netrevert.timer >/dev/null 2>&1 || true
  state_write status=confirmed previous="$(state_get previous)" \
    new="$(state_get new)" confirmed_at="$(ts)"
  log "CONFIRM ok new='$(state_get new)'"
  echo "confirmed"
}

cmd_check() {  # revert iff still pending. Safe to run any number of times.
  if [ "$(state_get status)" = "pending" ]; then
    do_revert
    echo "reverted"
  else
    echo "noop"
  fi
}

cmd_status() {
  if [ -f "$STATE_FILE" ]; then cat "$STATE_FILE"; else echo "{}"; fi
}

case "${1:-}" in
  apply)   shift; cmd_apply "$@" ;;
  confirm) cmd_confirm ;;
  check)   cmd_check ;;        # root/systemd only (timer + boot unit)
  status)  cmd_status ;;
  *) echo "usage: $0 {apply <ssid> | confirm | check | status}" >&2; exit 1 ;;
esac
