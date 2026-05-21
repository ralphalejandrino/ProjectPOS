#!/bin/bash
# FEATURE-017: dual-target offsite backup (a: local USB)
# Future sub-commits add GCS upload and offline queue.
set -euo pipefail

BACKUP_DIR=/home/ralph/TarsierPOS/backups
USB_MOUNT=/mnt/backup
AUDIT_LOG=/home/ralph/TarsierPOS/logs/backup-audit.log

mkdir -p "$(dirname "$AUDIT_LOG")"
ts() { date -Iseconds; }
log() { echo "$(ts) [offsite-backup] $*" | tee -a "$AUDIT_LOG"; }

latest=$(ls -1t "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | head -1 || true)
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

# GCS upload — sub-commit (b)
# Offline queue — sub-commit (c)

log "end"
