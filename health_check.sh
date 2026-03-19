#!/bin/bash
# TarsierPOS — Health watchdog (runs every 5 minutes via cron)
# Restarts services if they fail to respond, logs all events.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="/var/log/tarsierpos-health.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
RESTART_COUNT_FILE="/tmp/tarsierpos-restart-count"
MAX_RESTARTS=5

# Ensure log file exists and is writable
touch "$LOG" 2>/dev/null || LOG="/tmp/tarsierpos-health.log"

# ── Disk space check ────────────────────────────────────────────────────────
DISK_USAGE=$(df "$SCRIPT_DIR" | awk 'NR==2 {print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 90 ]; then
    echo "[$TIMESTAMP] ALERT: Disk usage at ${DISK_USAGE}% — transactions will fail if disk fills completely" >> "$LOG"
fi

backend_ok=false
frontend_ok=false

# ── Django backend ────────────────────────────────────────────────────────────
HEALTH_RESPONSE=$(curl -sf --max-time 5 http://localhost:9000/api/health/ 2>/dev/null || true)
if echo "$HEALTH_RESPONSE" | grep -q '"status":"ok"'; then
    backend_ok=true
fi

# ── Frontend static server ────────────────────────────────────────────────────
if curl -sf --max-time 5 http://localhost:8081/ >/dev/null 2>&1; then
    frontend_ok=true
fi

# ── Act on results ────────────────────────────────────────────────────────────
if $backend_ok && $frontend_ok; then
    echo "[$TIMESTAMP] OK" >> "$LOG"
    rm -f "$RESTART_COUNT_FILE"
else
    count=$(cat "$RESTART_COUNT_FILE" 2>/dev/null || echo 0)
    if [ "$count" -ge "$MAX_RESTARTS" ]; then
        echo "[$TIMESTAMP] ALERT: $count restarts triggered — stopping auto-restart. Manual intervention required." >> "$LOG"
        exit 0
    fi
    echo $((count + 1)) > "$RESTART_COUNT_FILE"

    if ! $backend_ok; then
        systemctl restart tarsierpos-backend 2>/dev/null || true
        echo "[$TIMESTAMP] RESTARTED backend (was not responding)" >> "$LOG"
    fi
    if ! $frontend_ok; then
        systemctl restart tarsierpos-frontend 2>/dev/null || true
        echo "[$TIMESTAMP] RESTARTED frontend (was not responding)" >> "$LOG"
    fi
fi

exit 0
