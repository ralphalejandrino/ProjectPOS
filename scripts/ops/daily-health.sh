#!/bin/bash
# FEATURE-027: daily health summary -> systemd journal (no external email).
# Reports disk usage, last backup timestamp, cert expiry days, service status.
# Portable: no hardcoded paths or service names.
#   Repo root  -> $TARSIERPOS_DIR (env) or this script's location.
#   Service    -> $TARSIERPOS_SERVICE from .env (default tarsierpos).
set -uo pipefail

TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/../.." && pwd)}

# 🔴 OPS-003: .env is parsed as DATA, never executed — `. .env` dies on the
# unquoted `(` in the Django secret before setting anything, which is what made
# this check report two FALSE warnings daily and drown the one true one.
# Shared implementation so the next script cannot repeat it.
_LIBDIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
for _c in "$_LIBDIR/../lib/load-env.sh" "$TARSIERPOS_DIR/scripts/lib/load-env.sh"; do
  [ -f "$_c" ] && { . "$_c"; break; }
done
tarsierpos_load_env "$TARSIERPOS_DIR/.env" TARSIERPOS_SERVICE TAILSCALE_HOSTNAME TARSIERPOS_BACKUP_DIR

# 🔴 FIX 2026-08-15: default was `tarsierpos-backend` — the DEV box's unit name.
# The client box runs `tarsierpos.service`, so this reported "not active" every
# day about a service that was running perfectly.
SERVICE=${TARSIERPOS_SERVICE:-tarsierpos}
# 🔴 FIX 2026-08-15: was "$TARSIERPOS_DIR/backups" (inside the repo, unwritable
# by the service user). Must match backup_db.sh or health reports on a directory
# nothing writes to.
BACKUP_DIR="${TARSIERPOS_BACKUP_DIR:-$(dirname "$TARSIERPOS_DIR")/backups}"
CERT_DIR="$TARSIERPOS_DIR/certs"

echo "===== TarsierPOS daily health summary $(date -Iseconds) ====="

# 1. Disk usage of the install tree + filesystem headroom.
used=$(du -sh "$TARSIERPOS_DIR" 2>/dev/null | cut -f1)
echo "disk: $TARSIERPOS_DIR uses ${used:-?}"
df -h "$TARSIERPOS_DIR" 2>/dev/null | awk 'NR==2 {print "disk: filesystem "$5" used, "$4" free on "$6}'

# 2. Last local backup snapshot.
last=$(ls -1t "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | head -1)
if [ -n "$last" ]; then
  age_h=$(( ( $(date +%s) - $(date -r "$last" +%s 2>/dev/null || echo 0) ) / 3600 ))
  # A backup that stopped running is far more dangerous than one that never
  # started: the folder still looks populated. Age it explicitly.
  if [ "$age_h" -gt 48 ]; then
    echo "backup: WARNING newest snapshot is ${age_h}h old — $(basename "$last")"
  else
    echo "backup: OK $(basename "$last") @ $(date -r "$last" -Iseconds 2>/dev/null) (${age_h}h old)"
  fi
else
  echo "backup: WARNING no local snapshots in $BACKUP_DIR"
fi
# backup_db.sh writes this on every run; surface a FAIL verbatim so the reason
# appears in the summary instead of only in the unit's own journal.
if [ -f "$BACKUP_DIR/.backup_status" ]; then
  echo "backup: last run -> $(head -c 300 "$BACKUP_DIR/.backup_status")"
else
  echo "backup: WARNING no .backup_status — backup_db.sh has not completed a run"
fi

# 3. Tailscale cert expiry (days remaining).
# 🔴 FIX 2026-08-15: this depended entirely on TAILSCALE_HOSTNAME, which the
# broken .env sourcing never set — so it cried "cert not found" every day while
# the cert sat right there in certs/. Fall back to discovering the newest .crt
# in CERT_DIR. Three lines of this summary were warnings and only ONE was true;
# that is what trained everyone to ignore it, and it is why a month of failed
# backups went unnoticed. A health check is only worth having if its warnings
# mean something.
crt="$CERT_DIR/${TAILSCALE_HOSTNAME:-}.crt"
if [ -z "${TAILSCALE_HOSTNAME:-}" ] || [ ! -f "$crt" ]; then
  crt=$(ls -1t "$CERT_DIR"/*.crt 2>/dev/null | head -1)
fi
if [ -n "${crt:-}" ] && [ -f "$crt" ]; then
  na=$(openssl x509 -enddate -noout -in "$crt" 2>/dev/null | cut -d= -f2)
  if [ -n "$na" ]; then
    days=$(( ( $(date -d "$na" +%s) - $(date +%s) ) / 86400 ))
    if [ "$days" -lt 30 ]; then
      echo "cert: WARNING expires in $days day(s) ($na)"
    else
      echo "cert: OK $days day(s) left ($na)"
    fi
  else
    echo "cert: WARNING could not parse NotAfter from $crt"
  fi
else
  echo "cert: WARNING no .crt found in $CERT_DIR (TAILSCALE_HOSTNAME=${TAILSCALE_HOSTNAME:-unset})"
fi

# 4. Backend service status.
if systemctl is-active --quiet "$SERVICE"; then
  echo "service: $SERVICE active"
else
  echo "service: WARNING $SERVICE is not active"
fi

echo "===== end ====="
