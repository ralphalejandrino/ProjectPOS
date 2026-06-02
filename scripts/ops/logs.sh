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
if [ -f "$TARSIERPOS_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090,SC1091
  . "$TARSIERPOS_DIR/.env"
  set +a
fi
SERVICE=${TARSIERPOS_SERVICE:-tarsierpos-backend}

# Default to today's logs when no args are given.
if [ "$#" -eq 0 ]; then
  set -- --since today
fi

exec journalctl -u "$SERVICE" --no-hostname -o short-iso "$@"
