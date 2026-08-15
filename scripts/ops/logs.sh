#!/bin/bash
# FEATURE-027: clean backend log view. Wraps journalctl for the backend unit so
# you don't have to remember the per-box service name.
# Portable: service name from $TARSIERPOS_SERVICE (.env), default tarsierpos-backend.
#
#   scripts/ops/logs.sh                 # today's logs
#   scripts/ops/logs.sh -f              # follow live
#   scripts/ops/logs.sh --since "1 hour ago"
set -uo pipefail

TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/../.." && pwd)}
# OPS-003: parse .env as DATA, never execute it. See scripts/lib/load-env.sh --
# `. .env` makes bash evaluate a file that contains an unquoted `(` in the Django
# secret, which aborts the script before any variable is set.
_LIBDIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
for _c in "$_LIBDIR/../lib/load-env.sh" "$_LIBDIR/../../scripts/lib/load-env.sh" "$TARSIERPOS_DIR/scripts/lib/load-env.sh"; do
  [ -f "$_c" ] && { . "$_c"; break; }
done
tarsierpos_load_env "$TARSIERPOS_DIR/.env" TARSIERPOS_SERVICE TAILSCALE_HOSTNAME
SERVICE=${TARSIERPOS_SERVICE:-tarsierpos-backend}

# Default to today's logs when no args are given.
if [ "$#" -eq 0 ]; then
  set -- --since today
fi

exec journalctl -u "$SERVICE" --no-hostname -o short-iso "$@"
