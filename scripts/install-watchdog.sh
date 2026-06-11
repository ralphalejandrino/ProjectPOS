#!/bin/bash
# TarsierPOS — idempotent installer for the FEATURE-045 connectivity watchdog.
# Follows the install-ops-units.sh pattern: everything version-controlled in
# the repo, nothing hand-installed (the FLAG-072 netguard lesson).
#
# Installs:
#   /usr/local/sbin/tarsier-watchdog        (root-owned copy of the script)
#   /usr/local/sbin/tarsier-boot-evidence
#   /etc/systemd/system/tarsier-watchdog.service + .timer  (every 2 min)
#   /etc/systemd/system/tarsier-boot-evidence.service      (once per boot)
#   /etc/logrotate.d/tarsier-connectivity                  (14 days)
#   /var/log/tarsier/                                      (evidence log dir)
#
# Safe on the dev box: the watchdog itself no-ops checks/actions for missing
# binaries; this installer only lays files down and enables units.
#
# Usage:  sudo bash scripts/install-watchdog.sh
set -euo pipefail

# Repo root: this script lives in scripts/, so root is one level up.
TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/.." && pwd)}
SRC_UNITS="$TARSIERPOS_DIR/scripts/systemd"
DST_UNITS="/etc/systemd/system"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo (installs to /usr/local/sbin and $DST_UNITS)." >&2
  exit 1
fi

echo "TARSIERPOS_DIR=$TARSIERPOS_DIR"

# Root-owned script copies — units must never execute the user-writable repo
# path as root (netguard precedent).
install -m 0755 -o root -g root \
  "$TARSIERPOS_DIR/scripts/network/tarsier-watchdog.sh" \
  /usr/local/sbin/tarsier-watchdog
echo "  installed: /usr/local/sbin/tarsier-watchdog"

install -m 0755 -o root -g root \
  "$TARSIERPOS_DIR/scripts/network/tarsier-boot-evidence.sh" \
  /usr/local/sbin/tarsier-boot-evidence
echo "  installed: /usr/local/sbin/tarsier-boot-evidence"

# Evidence log directory.
install -d -m 0755 -o root -g root /var/log/tarsier
echo "  ensured: /var/log/tarsier"

# Units (no __TARSIERPOS_DIR__ templating needed — they exec the sbin copies).
UNITS=(
  tarsier-watchdog.service
  tarsier-watchdog.timer
  tarsier-boot-evidence.service
)
for u in "${UNITS[@]}"; do
  install -m 0644 -o root -g root "$SRC_UNITS/$u" "$DST_UNITS/$u"
  echo "  installed: $DST_UNITS/$u"
done

# Logrotate (14 days).
install -m 0644 -o root -g root \
  "$TARSIERPOS_DIR/scripts/logrotate/tarsier-connectivity" \
  /etc/logrotate.d/tarsier-connectivity
echo "  installed: /etc/logrotate.d/tarsier-connectivity"

systemctl daemon-reload
systemctl enable --now tarsier-watchdog.timer
echo "  enabled+started: tarsier-watchdog.timer"
# Enable for future boots and run once now so the log gets an initial dump.
systemctl enable --now tarsier-boot-evidence.service
echo "  enabled+run: tarsier-boot-evidence.service"

echo "Done. Verify with:"
echo "  systemctl list-timers | grep tarsier-watchdog"
echo "  tail -50 /var/log/tarsier/connectivity.log"
