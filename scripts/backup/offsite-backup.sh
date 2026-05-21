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
  delay=10
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
    log "gcs ABORT all 3 attempts failed — queue for retry (sub-commit c)"
    # sub-commit (c) will write the file path into a pending queue here
  fi
else
  log "gcs SKIP GCS_DEST not configured"
fi

# Offline queue — sub-commit (c)

log "end"
