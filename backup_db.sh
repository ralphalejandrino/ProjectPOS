#!/bin/bash
# TarsierPOS — SQLite backup via Python's online-backup API (NO sqlite3 CLI).
#
# #1 backup fix (2026-07-18): the previous version shelled out to the `sqlite3`
# CLI — which is NOT installed on the minimized client box — and then ran
# `exit 0` unconditionally, so systemd recorded SUCCESS every night while
# writing ZERO backups (silent-success failure; the live register had no
# working automated backup for weeks). This version uses Python's stdlib
# sqlite3 online-backup (always present, WAL-safe, consistent on a live DB) and
# FAILS LOUD: any failure exits non-zero and records it in a status file the
# daily-health check can read. It still never hangs.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_SRC="$SCRIPT_DIR/db.sqlite3"
# #2 backup fix (2026-08-15): BACKUP_DIR pointed INSIDE the repo
# ("$SCRIPT_DIR/backups"), which the service user cannot write — the repo is
# tarsier:tarsier mode 750 and the unit runs as posadmin. So `mkdir -p` failed,
# the sqlite destination could not be created ("unable to open database file"),
# and the status file could not be written either. Result: 25 runs, 0 successes,
# no automated backup on the live register from 2026-07-19 to 2026-08-15.
# Now defaults to the SIBLING dir /opt/tarsierpos/backups — where the manual
# pre-deploy snapshots already live — and is overridable for other installs.
BACKUP_DIR="${TARSIERPOS_BACKUP_DIR:-$(dirname "$SCRIPT_DIR")/backups}"
LOG_DIR="$SCRIPT_DIR/logs"
LOG="$LOG_DIR/backup.log"
STATUS="$BACKUP_DIR/.backup_status"          # "OK <ts> ..." | "FAIL <ts> <reason>"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
FILENAME="db_$(date '+%Y%m%d_%H%M%S').sqlite3"
DEST="$BACKUP_DIR/$FILENAME"

mkdir -p "$LOG_DIR" 2>/dev/null || true
touch "$LOG" 2>/dev/null || LOG="/tmp/tarsierpos-backup.log"
mkdir -p "$BACKUP_DIR" 2>/dev/null || true

# Prefer the app venv's Python; fall back to system python3. Either has the
# stdlib sqlite3 module — that is the whole point (the CLI is what was missing).
PY="$SCRIPT_DIR/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

fail() {
    echo "[$TIMESTAMP] ERROR: $1" >> "$LOG"
    echo "FAIL $TIMESTAMP $1" > "$STATUS" 2>/dev/null || true
    rm -f "$DEST" 2>/dev/null || true
    exit 1
}

[ -f "$DB_SRC" ] || fail "db.sqlite3 not found at $DB_SRC"
[ -n "$PY" ]     || fail "no python interpreter found (venv or python3)"

# #2 backup fix: probe writability EXPLICITLY and fail with the exact remedy.
# The old code swallowed the mkdir error with `|| true` and then died deep in
# sqlite with "unable to open database file", which reads like a corrupt DB
# rather than a permissions problem — and buried the one thing an operator
# needs to know. A backup that cannot be written must say so in one line.
if [ ! -d "$BACKUP_DIR" ] || [ ! -w "$BACKUP_DIR" ]; then
    fail "backup dir not writable by $(id -un): $BACKUP_DIR — fix with: sudo install -d -o $(id -un) -g $(id -gn) $BACKUP_DIR"
fi

# Consistent online backup + integrity check, entirely in Python (no CLI).
if ! "$PY" - "$DB_SRC" "$DEST" <<'PYEOF'
import sqlite3, sys
src, dest = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dest)
try:
    with d:
        s.backup(d)                     # online backup API — safe on a live WAL DB
    row = d.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        print("integrity_check=%r" % (row,), file=sys.stderr)
        sys.exit(3)
finally:
    d.close()
    s.close()
PYEOF
then
    fail "online-backup failed for $DEST (see $LOG)"
fi

[ -s "$DEST" ] || fail "backup file missing or empty: $DEST"

# FEATURE-024 GFS retention — keep 7 daily + 4 weekly db_*.sqlite3; manual
# pre-*/pre-deploy-* snapshots are ignored by the selector.
GFS="$SCRIPT_DIR/scripts/backup/gfs_select.py"
if [ -f "$GFS" ]; then
    ls -1 "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null \
      | "$PY" "$GFS" 2>/dev/null \
      | while IFS= read -r old; do [ -n "$old" ] && rm -f "$old" 2>/dev/null || true; done
else
    # Fallback: keep the 7 most recent if the selector is unavailable.
    ls -t "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | tail -n +8 | xargs -r rm -f 2>/dev/null || true
fi

KEPT=$(ls "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | wc -l)
SIZE=$(stat -c%s "$DEST" 2>/dev/null || echo '?')
echo "[$TIMESTAMP] Backed up to $FILENAME — integrity OK (${SIZE} bytes, ${KEPT} kept, GFS 7d/4w)" >> "$LOG"
echo "OK $TIMESTAMP $FILENAME ${SIZE}b ${KEPT}kept" > "$STATUS" 2>/dev/null || true
exit 0
