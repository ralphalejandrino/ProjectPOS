#!/usr/bin/env bash
#
# dev.sh — local development launcher for TarsierPOS on WSL2.
#
# Serves the API *and* the static PWA frontend on a single origin so the app is
# viewable at http://localhost:8000/ (the frontend calls the API via relative
# paths, so they must share an origin — see pos_config/urls.py DEBUG block).
#
# Why this exists: production serves the frontend via nginx and runs the app as
# the `tarsierpos` systemd service; the WSL2 dev box has neither, so `runserver`
# alone returned 404 on /. This script sets the dev environment and starts the
# server in one command.
#
# It exports DEBUG/LOG_FILE *before* Django loads, so these win over the
# (production-shaped) values in .env without modifying that file — python-dotenv
# does not override variables already present in the environment.
#
# Usage:
#   ./dev.sh                 # serves on 0.0.0.0:8000
#   ./dev.sh 127.0.0.1:8001  # custom host:port
#
set -euo pipefail
cd "$(dirname "$0")"

export DEBUG=True
export LOG_FILE="${LOG_FILE:-$PWD/logs/dev.log}"
mkdir -p "$(dirname "$LOG_FILE")"

# shellcheck disable=SC1091
source .venv/bin/activate

BIND="${1:-0.0.0.0:8000}"
echo "TarsierPOS dev server — DEBUG=$DEBUG, log=$LOG_FILE"
echo "Open http://localhost:${BIND##*:}/ (use localhost, not the Tailscale host,"
echo "so the quick-login grid shows)."
exec python manage.py runserver "$BIND"
