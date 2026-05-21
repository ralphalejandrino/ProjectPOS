#!/bin/bash
# FEATURE-017(c): hourly retry of GCS uploads that aborted in offsite-backup.sh.
set -euo pipefail

AUDIT_LOG=${AUDIT_LOG:-/home/ralph/TarsierPOS/logs/backup-audit.log}
QUEUE_DIR=${QUEUE_DIR:-/home/ralph/TarsierPOS/logs/backup-queue}
BACKOFF_INITIAL=${BACKOFF_INITIAL:-10}

mkdir -p "$(dirname "$AUDIT_LOG")" "$QUEUE_DIR"
ts() { date -Iseconds; }
log() { echo "$(ts) [retry-queue] $*" | tee -a "$AUDIT_LOG"; }

if [[ -z "${GCS_DEST:-}" ]]; then
  log "gcs RETRY SKIP GCS_DEST not configured"
  exit 0
fi

shopt -s nullglob
pending=("$QUEUE_DIR"/*.pending)
shopt -u nullglob

if [[ ${#pending[@]} -eq 0 ]]; then
  log "queue empty"
  exit 0
fi

# Oldest-first (filenames are YYYYMMDDTHHMMSS.pending → lex == chrono)
IFS=$'\n' pending=($(printf '%s\n' "${pending[@]}" | sort))
unset IFS

for queue_file in "${pending[@]}"; do
  source_path=$(head -n1 "$queue_file" || true)
  if [[ -z "$source_path" ]]; then
    log "gcs RETRY SKIP empty queue file $queue_file — removing"
    rm -f "$queue_file"
    continue
  fi
  if [[ ! -f "$source_path" ]]; then
    log "gcs RETRY SKIP source gone $source_path (queue=$queue_file) — removing"
    rm -f "$queue_file"
    continue
  fi

  log "gcs RETRY begin queue=$queue_file source=$source_path"
  ok=0
  delay=$BACKOFF_INITIAL
  for attempt in 1 2 3; do
    if gsutil cp "$source_path" "${GCS_DEST%/}/$(basename "$source_path")"; then
      log "gcs RETRY OK attempt=$attempt dest=${GCS_DEST%/}/$(basename "$source_path")"
      rm -f "$queue_file"
      ok=1
      break
    else
      rc=$?
      log "gcs RETRY FAIL attempt=$attempt/3 (exit $rc), waiting ${delay}s"
      sleep "$delay"
      delay=$((delay * 2))
    fi
  done
  if [[ $ok -eq 0 ]]; then
    log "gcs RETRY ABORT keeping $queue_file for next hour"
  fi
done
