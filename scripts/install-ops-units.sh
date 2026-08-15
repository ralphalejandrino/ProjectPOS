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
# OPS-002 (2026-08-15): units that TOUCH THE DATABASE must run as its OWNER, not
# as whoever happened to run this installer. On pos-01 the installer was run
# by posadmin, so the backup unit got User=posadmin -- a user that cannot write
# db.sqlite3. The DB is WAL, and a WAL reader must be able to create the -shm
# sidecar, so the backup failed with "attempt to write a readonly database"
# whenever that sidecar was absent. 25 runs, 0 successes, a month with no backup.
# On a dev box there is no `tarsier`, so fall back to the deploy user.
if id -u tarsier >/dev/null 2>&1; then APP_USER=tarsier; else APP_USER="$DEPLOY_USER"; fi
SRC="$TARSIERPOS_DIR/scripts/systemd"
DST="/etc/systemd/system"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo (installs to $DST)." >&2
  exit 1
fi

# FIX-PENDING-18: nginx (www-data) must be in the tarsier group to read
# /opt/tarsierpos. Missing this is the silent failure mode on fresh installs
# (static assets 403, blank POS). Idempotent; only runs where a `tarsier` group
# exists (the production deploy model — skipped on dev boxes that run as the
# login user). Requires logout / `newgrp tarsier` for the current shell, but
# systemd picks the new group up on the next service start, so run before
# starting nginx.
if getent group tarsier >/dev/null 2>&1; then
  if id -nG www-data 2>/dev/null | tr ' ' '\n' | grep -qx tarsier; then
    echo "  www-data already in tarsier group"
  else
    usermod -aG tarsier www-data
    echo "  added www-data to tarsier group (restart nginx to take effect)"
  fi
else
  echo "  skip usermod: no 'tarsier' group on this box (dev model)"
fi

# B11b ops units (cert renewal, time-anchored local backup, daily health).
UNITS=(
  tarsierpos-cert-renew.service   tarsierpos-cert-renew.timer
  tarsierpos-backup-local.service tarsierpos-backup-local.timer
  tarsierpos-daily-health.service tarsierpos-daily-health.timer
)

echo "TARSIERPOS_DIR=$TARSIERPOS_DIR"
echo "DEPLOY_USER=$DEPLOY_USER"
echo "APP_USER=$APP_USER  (units touching the DB run as this)"

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
      -e "s|__APP_USER__|$APP_USER|g" \
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
