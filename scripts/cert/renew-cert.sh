#!/bin/bash
# FEATURE-018: Tailscale cert renewal (portable, no hardcoded paths or hostname).
# Run monthly by tarsierpos-cert-renew.timer. Renews the Tailscale TLS cert,
# reloads nginx, and logs a WARNING if the renewed cert is < 30 days from expiry.
#
# Portability:
#   * Repo root  -> $TARSIERPOS_DIR (env) or derived from this script's location.
#   * Hostname   -> $TAILSCALE_HOSTNAME from .env. If unset: log error, exit 1.
set -euo pipefail

TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/../.." && pwd)}
CERT_DIR="$TARSIERPOS_DIR/certs"
LOG_DIR="$TARSIERPOS_DIR/logs"
LOG="$LOG_DIR/cert-renew.log"

mkdir -p "$LOG_DIR"
ts() { date -Iseconds; }
log() { echo "$(ts) [cert-renew] $*" | tee -a "$LOG"; }

# Load .env for TAILSCALE_HOSTNAME (the backend service also loads it, but this
# script may run standalone via the timer).
# OPS-003: parse .env as DATA, never execute it. See scripts/lib/load-env.sh --
# `. .env` makes bash evaluate a file that contains an unquoted `(` in the Django
# secret, which aborts the script before any variable is set.
_LIBDIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
for _c in "$_LIBDIR/../lib/load-env.sh" "$_LIBDIR/../../scripts/lib/load-env.sh" "$TARSIERPOS_DIR/scripts/lib/load-env.sh"; do
  [ -f "$_c" ] && { . "$_c"; break; }
done
tarsierpos_load_env "$TARSIERPOS_DIR/.env" TAILSCALE_HOSTNAME

if [ -z "${TAILSCALE_HOSTNAME:-}" ]; then
  log "ERROR: TAILSCALE_HOSTNAME is unset — set it in $TARSIERPOS_DIR/.env. Aborting (no fallback hostname)."
  exit 1
fi

mkdir -p "$CERT_DIR"
CRT="$CERT_DIR/${TAILSCALE_HOSTNAME}.crt"
KEY="$CERT_DIR/${TAILSCALE_HOSTNAME}.key"

log "renewing cert for $TAILSCALE_HOSTNAME (min-validity 720h) -> $CRT"
if tailscale cert --min-validity=720h --cert-file "$CRT" --key-file "$KEY" "$TAILSCALE_HOSTNAME" 2>>"$LOG"; then
  log "cert renewal OK"
else
  rc=$?
  log "ERROR: 'tailscale cert' failed (exit $rc)"
  exit "$rc"
fi

# Reload nginx so it serves the renewed cert without dropping connections.
if systemctl reload nginx 2>>"$LOG"; then
  log "nginx reloaded"
else
  log "WARNING: nginx reload failed — check 'systemctl status nginx'"
fi

# Expiry check: WARN if the (freshly renewed) cert is still < 30 days out, which
# would mean the renewal did not actually extend validity.
NOTAFTER=$(openssl x509 -enddate -noout -in "$CRT" 2>/dev/null | cut -d= -f2 || true)
if [ -n "$NOTAFTER" ]; then
  END=$(date -d "$NOTAFTER" +%s 2>/dev/null || echo 0)
  DAYS=$(( (END - $(date +%s)) / 86400 ))
  if [ "$DAYS" -lt 30 ]; then
    log "WARNING: cert still expires in $DAYS day(s) (NotAfter=$NOTAFTER) — renewal may have failed"
  else
    log "cert valid for $DAYS more day(s) (NotAfter=$NOTAFTER)"
  fi
else
  log "WARNING: could not parse cert NotAfter from $CRT"
fi
