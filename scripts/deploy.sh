#!/bin/bash
# deploy.sh — pull, migrate safely, restart, and VERIFY the live build.
#
# Built to catch the BUG-017 class of failure: code committed + promoted to main
# but never actually live on prod (clients silently ran stale code). The verify
# step asserts the Service-Worker cache version that nginx is *serving* matches
# the version in the repo — and exits non-zero, loudly, if it doesn't.
#
# Portable (no hardcoded paths/service names — same convention as ops scripts):
#   Repo root -> $TARSIERPOS_DIR (env) or derived from this script's location.
#   Service   -> $TARSIERPOS_SERVICE from .env (default tarsierpos-backend).
#   Host      -> $TAILSCALE_HOSTNAME from .env (verify falls back to localhost).
# Run ON the box that serves the POS. Needs passwordless sudo for
# `systemctl restart <service>` (see scripts/sudoers/).
#
# Usage:
#   scripts/deploy.sh              # full deploy + verify
#   scripts/deploy.sh verify       # verify only — "is prod actually current?"
#   scripts/deploy.sh --no-migrate # deploy but skip migrations
set -uo pipefail

# ---- resolve environment --------------------------------------------------
TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/.." && pwd)}
cd "$TARSIERPOS_DIR" || { echo "FATAL: cannot cd to $TARSIERPOS_DIR"; exit 1; }
if [ -f "$TARSIERPOS_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090,SC1091
  . "$TARSIERPOS_DIR/.env"
  set +a
fi
SERVICE=${TARSIERPOS_SERVICE:-tarsierpos-backend}
PY="$TARSIERPOS_DIR/venv/bin/python"
PIP="$TARSIERPOS_DIR/venv/bin/pip"
SW_FILE="$TARSIERPOS_DIR/frontend/public/sw.js"
HOST=${TAILSCALE_HOSTNAME:-localhost}

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YEL=$'\033[0;33m'; NC=$'\033[0m'
say()  { echo "${YEL}[deploy]${NC} $*"; }
ok()   { echo "${GREEN}[deploy] ✓ $*${NC}"; }
fail() {
  echo "${RED}[deploy] ✗ DEPLOY FAILED: $*${NC}" >&2
  [ -n "${ROLLBACK_TO:-}" ] && echo "${RED}  rollback: git -C \"$TARSIERPOS_DIR\" reset --hard $ROLLBACK_TO && sudo systemctl restart $SERVICE${NC}" >&2
  exit 1
}

repo_sw_version()   { grep -oE 'tarsierpos-v[0-9]+' "$SW_FILE" | head -1; }
served_sw_version() {
  local h v
  for h in localhost "$HOST"; do
    v=$(curl -sk --max-time 10 "https://$h/sw.js" 2>/dev/null | grep -oE 'tarsierpos-v[0-9]+' | head -1)
    [ -n "$v" ] && { echo "$v"; return 0; }
  done
  return 1
}

# ---- verify: the whole point of this script -------------------------------
verify() {
  local expected served code
  expected=$(repo_sw_version) || fail "cannot read SW version from $SW_FILE"
  [ -n "$expected" ] || fail "no CACHE_NAME (tarsierpos-vN) found in $SW_FILE"

  systemctl is-active --quiet "$SERVICE" || fail "service '$SERVICE' is not active"

  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "https://localhost/" 2>/dev/null)
  [ "$code" = "200" ] || fail "POS root did not return 200 (got '${code:-none}')"

  served=$(served_sw_version) || fail "could not fetch live sw.js over HTTPS"

  if [ "$served" != "$expected" ]; then
    fail "LIVE MISMATCH — nginx serves $served but repo is $expected.
       Clients are running stale code. If the file on disk is correct, this is
       almost always a browser/SW cache; have a manager hard-refresh once. If it
       persists, the working tree was not actually updated."
  fi
  ok "live SW $served == repo $expected · $SERVICE active · HTTP 200"
}

# ---- modes ----------------------------------------------------------------
MODE=${1:-deploy}
case "$MODE" in
  verify) verify; exit 0 ;;
  --no-migrate) DO_MIGRATE=0 ;;
  deploy|"") DO_MIGRATE=1 ;;
  *) echo "usage: deploy.sh [verify|--no-migrate]"; exit 2 ;;
esac

# ---- deploy ---------------------------------------------------------------
command -v git >/dev/null || fail "git not found"
[ -x "$PY" ] || fail "venv python not found at $PY"

ROLLBACK_TO=$(git rev-parse --short HEAD) || fail "not a git repo at $TARSIERPOS_DIR"
say "current HEAD $ROLLBACK_TO on branch $(git rev-parse --abbrev-ref HEAD); fetching…"

git pull --ff-only || fail "git pull is not fast-forward — prod diverged from origin; resolve by hand"
after=$(git rev-parse --short HEAD)
if [ "$ROLLBACK_TO" = "$after" ]; then
  say "already up to date at $after — re-verifying live state anyway"
else
  say "updated $ROLLBACK_TO → $after"
fi

# dependencies, only if they changed
if git diff --name-only "$ROLLBACK_TO" "$after" | grep -qx 'requirements.txt'; then
  say "requirements.txt changed → pip install"
  "$PIP" install -r requirements.txt || fail "pip install failed (prod NOT restarted)"
fi

# migrations, only if pending — always via safe_migrate (snapshots first)
if [ "${DO_MIGRATE:-1}" = 1 ]; then
  if "$PY" manage.py migrate --check >/dev/null 2>&1; then
    say "no pending migrations"
  else
    say "pending migrations → safe_migrate (snapshots DB first)"
    "$PY" manage.py safe_migrate || fail "safe_migrate failed — see its rollback output; prod NOT restarted"
  fi
else
  say "migrations skipped (--no-migrate)"
fi

# Django static (admin etc.); the PWA is served directly so this is non-fatal
"$PY" manage.py collectstatic --noinput >/dev/null 2>&1 || say "collectstatic skipped/failed (non-fatal)"

say "restarting $SERVICE…"
sudo systemctl restart "$SERVICE" || fail "systemctl restart $SERVICE failed"
sleep 2

verify
ok "DEPLOY COMPLETE ($ROLLBACK_TO → $after). Managers may need one hard-refresh for the new SW."
