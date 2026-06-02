#!/bin/bash
# TarsierPOS — SQLite backup with 7-file rotation
# Called by ExecStopPost on every backend shutdown.
# Must never block — always exit 0.

# Resolve paths relative to this script's location
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_SRC="$SCRIPT_DIR/db.sqlite3"
BACKUP_DIR="$SCRIPT_DIR/backups"
LOG_DIR="$SCRIPT_DIR/logs"
LOG="$LOG_DIR/backup.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
FILENAME="db_$(date '+%Y%m%d_%H%M%S').sqlite3"
DEST="$BACKUP_DIR/$FILENAME"

# Ensure log dir + file exist; fall back to /tmp only if creation fails
mkdir -p "$LOG_DIR" 2>/dev/null || true
touch "$LOG" 2>/dev/null || LOG="/tmp/tarsierpos-backup.log"

# Create backup directory if needed
mkdir -p "$BACKUP_DIR" 2>/dev/null || true

# Bail early if source DB is missing
if [ ! -f "$DB_SRC" ]; then
    echo "[$TIMESTAMP] ERROR: db.sqlite3 not found at $DB_SRC" >> "$LOG"
    exit 0
fi

# Use SQLite backup API for safe, consistent backups (not cp)
if sqlite3 "$DB_SRC" ".backup '$DEST'" 2>/dev/null; then
    # Verify the backup file is a valid, readable SQLite database
    INTEGRITY=$(sqlite3 "$DEST" "PRAGMA integrity_check;" 2>/dev/null)
    if [ "$INTEGRITY" = "ok" ]; then
        # FEATURE-024: GFS retention — keep 7 daily + 4 weekly snapshots, prune
        # the rest. pre-migrate-*.sqlite3 snapshots are ignored by the selector.
        GFS="$SCRIPT_DIR/scripts/backup/gfs_select.py"
        if [ -f "$GFS" ] && command -v python3 >/dev/null 2>&1; then
            ls -1 "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null \
              | python3 "$GFS" 2>/dev/null \
              | while IFS= read -r old; do [ -n "$old" ] && rm -f "$old" 2>/dev/null || true; done
        else
            # Fallback: keep 7 most recent if the selector is unavailable.
            ls -t "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null || true
        fi
        KEPT=$(ls "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | wc -l)
        echo "[$TIMESTAMP] Backed up to $FILENAME — integrity OK ($KEPT kept, GFS 7d/4w)" >> "$LOG"
    else
        echo "[$TIMESTAMP] ERROR: Backup integrity check failed ($INTEGRITY) — deleting $FILENAME" >> "$LOG"
        rm -f "$DEST"
    fi
else
    echo "[$TIMESTAMP] ERROR: sqlite3 backup failed for $DEST" >> "$LOG"
fi

exit 0
