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

# OPS-003b (2026-08-15): DERIVE the hostname instead of demanding it.
# On pos-01 TAILSCALE_HOSTNAME is simply NOT PRESENT in .env, so this script
# aborted on every run even after the .env parse bug was fixed — and the cert was
# heading for expiry on 2026-09-03, which would have taken https://localhost, and
# therefore the kiosk, down. A renewal script that cannot work out which cert it
# is renewing — when the cert is sitting right there in CERT_DIR — is a script
# that fails for no good reason.
#
# Resolution order, most authoritative first:
#   1. TAILSCALE_HOSTNAME from .env       (explicit operator intent)
#   2. the existing cert's own filename   (definitive for a RENEWAL: it is
#                                          literally the cert being renewed)
#   3. tailscale's own view of this node  (fresh install, no cert yet)
if [ -z "${TAILSCALE_HOSTNAME:-}" ] && [ -d "$CERT_DIR" ]; then
  # NOTE the `|| _existing=""`: under `set -euo pipefail`, an unmatched glob
  # makes `ls` fail, pipefail propagates it through `| head`, and the assignment
  # then kills the script SILENTLY (exit 2, no log line). Caught by testing the
  # no-cert path — which is exactly the silent-failure shape this ticket exists
  # to remove, so it would have been galling to ship it.
  _existing=$(ls -1t "$CERT_DIR"/*.crt 2>/dev/null | head -1) || _existing=""
  if [ -n "$_existing" ]; then
    TAILSCALE_HOSTNAME=$(basename "$_existing" .crt)
    log "TAILSCALE_HOSTNAME not in .env — derived from the existing cert: $TAILSCALE_HOSTNAME"
  fi
fi
if [ -z "${TAILSCALE_HOSTNAME:-}" ] && command -v tailscale >/dev/null 2>&1; then
  TAILSCALE_HOSTNAME=$(tailscale status --json 2>/dev/null \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print((d.get('Self') or {}).get('DNSName','').rstrip('.'))" 2>/dev/null || true)
  [ -n "${TAILSCALE_HOSTNAME:-}" ] && log "TAILSCALE_HOSTNAME derived from tailscale: $TAILSCALE_HOSTNAME"
fi

if [ -z "${TAILSCALE_HOSTNAME:-}" ]; then
  log "ERROR: could not determine the Tailscale hostname — not in $TARSIERPOS_DIR/.env, no cert in $CERT_DIR, and tailscale gave nothing. Aborting."
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
