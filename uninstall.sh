#!/bin/bash
# TarsierPOS — Uninstaller
# WARNING: This will permanently delete the TarsierPOS installation and all data on this machine.
# Safe to run repeatedly — all steps ignore errors if components are not found.

set -e

echo ""
echo "=========================================="
echo "  TarsierPOS Uninstaller"
echo "=========================================="
echo ""
echo "  WARNING: This will permanently delete the TarsierPOS"
echo "  installation and ALL data on this machine."
echo ""
read -p "  Type CONFIRM to proceed: " confirm
if [ "$confirm" != "CONFIRM" ]; then
    echo "  Aborted."
    exit 0
fi
echo ""

DEPLOY_USER=${SUDO_USER:-$(whoami)}
PROJECT_DIR="/home/$DEPLOY_USER/TarsierPOS"

# ── 1. Stop and disable services ──────────────────────────────────────────────
echo "[1/6] Stopping and disabling services..."
systemctl stop tarsierpos-backend tarsierpos-frontend 2>/dev/null || true
systemctl disable tarsierpos-backend tarsierpos-frontend 2>/dev/null || true
systemctl stop pos-backend pos-frontend 2>/dev/null || true
systemctl disable pos-backend pos-frontend 2>/dev/null || true

# ── 2. Remove service files ───────────────────────────────────────────────────
echo "[2/6] Removing systemd service files..."
rm -f /etc/systemd/system/tarsierpos-backend.service
rm -f /etc/systemd/system/tarsierpos-frontend.service
rm -f /etc/systemd/system/pos-backend.service
rm -f /etc/systemd/system/pos-frontend.service
systemctl daemon-reload

# ── 3. Remove project directory (includes ADMIN_CREDENTIALS.txt, wheels/, db) ─
echo "[3/6] Removing project directory: $PROJECT_DIR"
rm -rf "$PROJECT_DIR"

# ── 4. Remove log files ───────────────────────────────────────────────────────
echo "[4/6] Removing log files..."
rm -f /var/log/tarsierpos-backup.log
rm -f /var/log/tarsierpos-health.log
rm -f /tmp/tarsierpos-backup.log
rm -f /tmp/tarsierpos-health.log

# ── 5. Remove cron watchdog entry ─────────────────────────────────────────────
echo "[5/6] Removing health-check cron job..."
(crontab -l 2>/dev/null | grep -v "health_check.sh") | crontab - 2>/dev/null || true

# ── 6. Drop PostgreSQL database and user (no-op if SQLite-only) ───────────────
echo "[6/6] Dropping PostgreSQL database and user (skipped silently if not present)..."
sudo -u postgres psql -c "DROP DATABASE IF EXISTS tarsierpos;" 2>/dev/null || true
sudo -u postgres psql -c "DROP USER IF EXISTS tarsierpos_user;" 2>/dev/null || true

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "  TarsierPOS uninstalled."
echo "  Machine is clean. Ready for fresh deployment."
echo "=========================================="
echo ""
