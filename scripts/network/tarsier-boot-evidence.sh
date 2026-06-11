#!/bin/bash
# =============================================================================
# FEATURE-045: tarsier-boot-evidence — one-shot boot-time connectivity dump.
#
# Runs once per boot (tarsier-boot-evidence.service) and appends to the same
# evidence log the watchdog writes (/var/log/tarsier/connectivity.log):
#
#   - nmcli connection list + autoconnect/priority table
#   - tailscale status (brief)
#   - last 50 journal lines of NetworkManager and tailscaled from this boot
#
# Purpose: when pos-01 comes up without Tailscale, remote support gets a
# post-hoc record of what the network stack did at boot instead of being blind.
#
# Dev-box safe: every section is guarded — a missing binary/unit logs a one-line
# skip, never fails the unit.
#
# Test hooks: TARSIER_WD_LOG overrides the log path;
#             NMCLI / TAILSCALE / JOURNALCTL override binaries.
# =============================================================================
set -euo pipefail

NMCLI="${NMCLI:-nmcli}"
TAILSCALE="${TAILSCALE:-tailscale}"
JOURNALCTL="${JOURNALCTL:-journalctl}"
LOG_FILE="${TARSIER_WD_LOG:-/var/log/tarsier/connectivity.log}"

mkdir -p "$(dirname "$LOG_FILE")"

{
  echo "================================================================"
  echo "$(date '+%Y-%m-%d %H:%M:%S%z') [boot-evidence] boot dump on $(hostname)"
  echo "================================================================"

  echo "--- nmcli connection show ---"
  if command -v "$NMCLI" >/dev/null 2>&1; then
    "$NMCLI" connection show 2>&1 || true
    echo "--- autoconnect priorities ---"
    "$NMCLI" -f NAME,TYPE,DEVICE,AUTOCONNECT,AUTOCONNECT-PRIORITY \
      connection show 2>&1 || true
  else
    echo "(nmcli not installed — skipped)"
  fi

  echo "--- tailscale status ---"
  if command -v "$TAILSCALE" >/dev/null 2>&1; then
    "$TAILSCALE" status 2>&1 || true
  else
    echo "(tailscale not installed — skipped)"
  fi

  echo "--- NetworkManager journal (this boot, last 50) ---"
  "$JOURNALCTL" -u NetworkManager -b -n 50 --no-pager 2>&1 || \
    echo "(journalctl NetworkManager unavailable)"

  echo "--- tailscaled journal (this boot, last 50) ---"
  "$JOURNALCTL" -u tailscaled -b -n 50 --no-pager 2>&1 || \
    echo "(journalctl tailscaled unavailable)"

  echo "[boot-evidence] dump complete"
} >>"$LOG_FILE" 2>&1

exit 0
