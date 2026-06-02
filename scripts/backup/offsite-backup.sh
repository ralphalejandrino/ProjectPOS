#!/bin/bash
# FEATURE-017: dual-target offsite backup (a: local USB)
# Future sub-commits add GCS upload and offline queue.
set -euo pipefail

# Portable: repo root from $TARSIERPOS_DIR (set by the unit) or this script's
# location. All defaults derive from it — no hardcoded user home.
TARSIERPOS_DIR=${TARSIERPOS_DIR:-$(cd "$(dirname "$(realpath "$0")")/../.." && pwd)}
BACKUP_DIR=${BACKUP_DIR:-$TARSIERPOS_DIR/backups}
USB_MOUNT=${USB_MOUNT:-/mnt/backup}
AUDIT_LOG=${AUDIT_LOG:-$TARSIERPOS_DIR/logs/backup-audit.log}
QUEUE_DIR=${QUEUE_DIR:-$TARSIERPOS_DIR/logs/backup-queue}
BACKOFF_INITIAL=${BACKOFF_INITIAL:-10}

mkdir -p "$(dirname "$AUDIT_LOG")" "$QUEUE_DIR"
ts() { date -Iseconds; }
log() { echo "$(ts) [offsite-backup] $*" | tee -a "$AUDIT_LOG"; }

latest=$(ls -1t "$BACKUP_DIR"/db_[0-9]*.sqlite3 2>/dev/null | head -1 || true)
if [[ -z "$latest" ]]; then
  log "FATAL no backup snapshot in $BACKUP_DIR — FIX-PENDING-15 may be broken"
  exit 1
fi

log "begin source=$latest size=$(stat -c %s "$latest")"

# Local USB target
if mountpoint -q "$USB_MOUNT"; then
  dest="$USB_MOUNT/$(basename "$latest")"
  if cp -p "$latest" "$dest"; then
    log "local OK dest=$dest"
  else
    log "local FAIL copy to $dest failed (exit $?)"
  fi
else
  log "local SKIP $USB_MOUNT not a mountpoint"
fi

# GCS upload (FEATURE-017(b))
if [[ -n "${GCS_DEST:-}" ]]; then
  gcs_ok=0
  delay=$BACKOFF_INITIAL
  for attempt in 1 2 3; do
    if gsutil cp "$latest" "${GCS_DEST%/}/$(basename "$latest")"; then
      log "gcs OK attempt=$attempt dest=${GCS_DEST%/}/$(basename "$latest")"
      gcs_ok=1
      break
    else
      rc=$?
      log "gcs FAIL attempt=$attempt/3 (exit $rc), waiting ${delay}s"
      sleep "$delay"
      delay=$((delay * 2))
    fi
  done
  if [[ $gcs_ok -eq 0 ]]; then
    log "gcs ABORT all 3 attempts failed"
    queue_file="$QUEUE_DIR/$(date +%Y%m%dT%H%M%S).pending"
    printf '%s\n' "$latest" > "$queue_file"
    log "gcs QUEUE wrote $queue_file"
  fi
else
  log "gcs SKIP GCS_DEST not configured"
fi

# GCS retention prune — mirror the local GFS policy (7 daily + 4 weekly).
# FEATURE-024. Destructive on the bucket but bounded to db_*.sqlite3 objects
# the selector marks for deletion; pre-migrate-* objects are never matched.
if [[ -n "${GCS_DEST:-}" ]]; then
  gfs="$TARSIERPOS_DIR/scripts/backup/gfs_select.py"
  if command -v python3 >/dev/null 2>&1 && [[ -f "$gfs" ]]; then
    objs=$(gsutil ls "${GCS_DEST%/}/db_*.sqlite3" 2>/dev/null || true)
    if [[ -n "$objs" ]]; then
      printf '%s\n' "$objs" | python3 "$gfs" 2>/dev/null | while IFS= read -r obj; do
        [[ -n "$obj" ]] || continue
        if gsutil rm "$obj" >/dev/null 2>&1; then
          log "gcs PRUNE $obj"
        else
          log "gcs PRUNE FAIL $obj"
        fi
      done
    fi
  else
    log "gcs PRUNE SKIP selector or python3 unavailable"
  fi
fi

# Offline queue — sub-commit (c)

log "end"
