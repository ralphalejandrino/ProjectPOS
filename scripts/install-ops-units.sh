#!/bin/bash
# TarsierPOS — portable installer for ops systemd units (B11b).
# Templates __TARSIERPOS_DIR__ / __DEPLOY_USER__ in the repo's unit files and
# installs them to /etc/systemd/system, then daemon-reload + enable --now the
# timers. Works on any machine/user — no hardcoded paths or home dirs.
#
# Usage:  sudo bash scripts/install-ops-units.sh
set -euo pipefail

# Repo root: this script lives in scripts/, so root is one level up.
TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/.." && pwd)}
DEPLOY_USER=${SUDO_USER:-$(whoami)}
SRC="$TARSIERPOS_DIR/scripts/systemd"
DST="/etc/systemd/system"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo (installs to $DST)." >&2
  exit 1
fi

# B11b ops units (cert renewal, time-anchored local backup, daily health).
UNITS=(
  tarsierpos-cert-renew.service   tarsierpos-cert-renew.timer
  tarsierpos-backup-local.service tarsierpos-backup-local.timer
  tarsierpos-daily-health.service tarsierpos-daily-health.timer
)

echo "TARSIERPOS_DIR=$TARSIERPOS_DIR"
echo "DEPLOY_USER=$DEPLOY_USER"

# Ensure scripts are executable (git preserves the bit, but be safe).
chmod +x "$TARSIERPOS_DIR"/scripts/cert/*.sh \
         "$TARSIERPOS_DIR"/scripts/ops/*.sh \
         "$TARSIERPOS_DIR"/backup_db.sh 2>/dev/null || true

for u in "${UNITS[@]}"; do
  if [ ! -f "$SRC/$u" ]; then
    echo "  skip (not found): $u"
    continue
  fi
  sed -e "s|__TARSIERPOS_DIR__|$TARSIERPOS_DIR|g" \
      -e "s|__DEPLOY_USER__|$DEPLOY_USER|g" \
      "$SRC/$u" > "$DST/$u"
  echo "  installed: $DST/$u"
done

systemctl daemon-reload

for u in "${UNITS[@]}"; do
  case "$u" in
    *.timer)
      if [ -f "$DST/$u" ]; then
        systemctl enable --now "$u"
        echo "  enabled+started: $u"
      fi
      ;;
  esac
done

echo "Done. Verify with: systemctl list-timers | grep tarsierpos"
