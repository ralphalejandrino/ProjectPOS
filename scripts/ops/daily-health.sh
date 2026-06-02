#!/bin/bash
# FEATURE-027: daily health summary -> systemd journal (no external email).
# Reports disk usage, last backup timestamp, cert expiry days, service status.
# Portable: no hardcoded paths or service names.
#   Repo root  -> $TARSIERPOS_DIR (env) or this script's location.
#   Service    -> $TARSIERPOS_SERVICE from .env (default tarsierpos-backend).
set -uo pipefail

TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/../.." && pwd)}
if [ -f "$TARSIERPOS_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090,SC1091
  . "$TARSIERPOS_DIR/.env"
  set +a
fi
SERVICE=${TARSIERPOS_SERVICE:-tarsierpos-backend}
BACKUP_DIR="$TARSIERPOS_DIR/backups"
CERT_DIR="$TARSIERPOS_DIR/certs"

echo "===== TarsierPOS daily health summary $(date -Iseconds) ====="

# 1. Disk usage of the install tree + filesystem headroom.
used=$(du -sh "$TARSIERPOS_DIR" 2>/dev/null | cut -f1)
echo "disk: $TARSIERPOS_DIR uses ${used:-?}"
df -h "$TARSIERPOS_DIR" 2>/dev/null | awk 'NR==2 {print "disk: filesystem "$5" used, "$4" free on "$6}'

# 2. Last local backup snapshot.
last=$(ls -1t "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | head -1)
if [ -n "$last" ]; then
  echo "backup: last local snapshot $(basename "$last") @ $(date -r "$last" -Iseconds 2>/dev/null)"
else
  echo "backup: WARNING no local snapshots in $BACKUP_DIR"
fi

# 3. Tailscale cert expiry (days remaining).
crt="$CERT_DIR/${TAILSCALE_HOSTNAME:-}.crt"
if [ -n "${TAILSCALE_HOSTNAME:-}" ] && [ -f "$crt" ]; then
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
  echo "cert: WARNING cert not found (TAILSCALE_HOSTNAME=${TAILSCALE_HOSTNAME:-unset})"
fi

# 4. Backend service status.
if systemctl is-active --quiet "$SERVICE"; then
  echo "service: $SERVICE active"
else
  echo "service: WARNING $SERVICE is not active"
fi

echo "===== end ====="
