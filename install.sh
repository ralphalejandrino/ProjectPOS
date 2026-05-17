#!/bin/bash
# TarsierPOS — One-command installer
# Usage: sudo bash install.sh
# Deploys from USB image to a fresh Ubuntu 24.04 OptiPlex.

set -euo pipefail

trap 'echo "" >&2
      echo "═══════════════════════════════════════" >&2
      echo "  INSTALL FAILED on line $LINENO" >&2
      echo "  To clean up: sudo bash $PROJ/uninstall.sh" >&2
      echo "═══════════════════════════════════════" >&2' ERR

DEPLOY_USER=${SUDO_USER:-$(whoami)}
if [ "$DEPLOY_USER" = "root" ]; then
    echo "ERROR: Do not install TarsierPOS as root. Run with: sudo bash install.sh" >&2
    echo "       (as a regular user, not logged in as root)" >&2
    exit 1
fi
PROJ="/home/$DEPLOY_USER/TarsierPOS"
VENV="$PROJ/venv"
SERVICE_DIR="/etc/systemd/system"

if [ -f "$PROJ/.env" ]; then
    echo ""
    echo "  ⚠️  WARNING: Existing TarsierPOS installation detected."
    echo "  Re-installing will generate NEW credentials."
    echo "  This will permanently invalidate all encrypted data"
    echo "  (payment gateway configs) from the previous install."
    echo ""
    read -p "  Type REINSTALL to continue, anything else to abort: " confirm
    if [ "$confirm" != "REINSTALL" ]; then
        echo "Aborted. Existing installation preserved."
        exit 0
    fi
    echo "  Proceeding with reinstall..."
fi

echo "=== TarsierPOS Installer ==="

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/11] Installing system packages..."
apt-get update -q
apt-get install -y python3 python3-pip python3-venv curl sqlite3

# ── 2. Python venv ─────────────────────────────────────────────────────────────
echo "[2/11] Setting up Python venv at $VENV..."
python3 -m venv "$VENV"

# ── 3. pip install ─────────────────────────────────────────────────────────────
echo "[3/11] Installing Python dependencies..."
"$VENV/bin/pip" install --upgrade pip -q
if [ -d "$PROJ/wheels" ] && [ "$(ls -A "$PROJ/wheels")" ]; then
    echo "  Installing from vendored wheels (offline)..."
    "$VENV/bin/pip" install \
        --no-index \
        --find-links="$PROJ/wheels/" \
        -r "$PROJ/requirements.txt"
else
    echo "  WARNING: No vendored wheels found, falling back to PyPI..."
    "$VENV/bin/pip" install -r "$PROJ/requirements.txt"
fi

# ── 4. Generate unique credentials for this unit ──────────────────────────────
echo "[4/11] Generating unique credentials for this unit..."

SECRET_KEY=$("$VENV/bin/python" -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits + '!@#\$%^&*') for _ in range(50)))")
FERNET_KEY=$("$VENV/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
DB_PASS=$("$VENV/bin/python" -c "import secrets, string; chars = string.ascii_letters + string.digits + '@#%^&*'; print(''.join(secrets.choice(chars) for _ in range(32)))")
LAN_IP=$(hostname -I | awk '{print $1}')
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "N/A")

printf 'DJANGO_SECRET_KEY=%s\n' "$SECRET_KEY"                          >  "$PROJ/.env"
printf 'FERNET_KEY=%s\n'        "$FERNET_KEY"                          >> "$PROJ/.env"
printf 'DB_PASSWORD=%s\n'       "$DB_PASS"                             >> "$PROJ/.env"
printf 'ALLOWED_HOSTS=%s\n'     "*" >> "$PROJ/.env"
printf 'BACKUP_PATH=%s\n'       "$PROJ/backups"                        >> "$PROJ/.env"
printf 'DEBUG=%s\n'             "False"                                >> "$PROJ/.env"

chown "$DEPLOY_USER:$DEPLOY_USER" "$PROJ/.env"
chmod 600 "$PROJ/.env"

# Verify .env integrity
ENV_LINES=$(grep -c "^[A-Z]" "$PROJ/.env")
KEY_LEN=${#SECRET_KEY}
if [ "$ENV_LINES" -ne 6 ]; then
    echo "  ERROR: .env has $ENV_LINES lines (expected 6)" >&2; exit 1
fi
if [ "$KEY_LEN" -ne 50 ]; then
    echo "  ERROR: SECRET_KEY is $KEY_LEN chars (expected 50)" >&2; exit 1
fi

echo "  ✓ Unique credentials generated and written to .env ($ENV_LINES vars, key=${KEY_LEN}ch)"
echo "  ✓ ALLOWED_HOSTS set to wildcard (LAN-only deployment)"
echo "  ℹ️  For security, configure a static LAN IP on this machine"
echo "     and update ALLOWED_HOSTS in $PROJ/.env after setup."
echo "  ✓ Unit LAN IP: $LAN_IP"
echo "  ✓ Tailscale IP: $TAILSCALE_IP"

# ── 3b. Patch django-fernet-fields for Django 4.x compatibility ───────────────
echo "[3b/11] Patching django-fernet-fields for Django 4.x (force_text → force_str)..."
FERNET_FIELDS_FILE="$VENV/lib/python3.12/site-packages/fernet_fields/fields.py"
if [ -f "$FERNET_FIELDS_FILE" ]; then
    sed -i 's/from django.utils.encoding import force_bytes, force_text/from django.utils.encoding import force_bytes\ntry:\n    from django.utils.encoding import force_text\nexcept ImportError:\n    from django.utils.encoding import force_str as force_text/' "$FERNET_FIELDS_FILE"
    echo "  ✓ Patched $FERNET_FIELDS_FILE"
else
    echo "  WARNING: fernet_fields not found at expected path — skipping patch" >&2
fi

# ── 5. Django migrate ──────────────────────────────────────────────────────────
echo "[5/11] Running database migrations..."
# Pre-create the log file as root so the deploy user can write to it
touch /var/log/tarsierpos-app.log
chown "$DEPLOY_USER:$DEPLOY_USER" /var/log/tarsierpos-app.log
chmod 640 /var/log/tarsierpos-app.log

cd "$PROJ"
sudo -u "$DEPLOY_USER" "$VENV/bin/python" manage.py migrate --noinput

# ── 5b. Create initial admin user ────────────────────────────────────────────
echo "[5b/11] Creating initial admin user..."

ADMIN_PASS=$("$VENV/bin/python" -c "import secrets, string; \
  chars = string.ascii_letters + string.digits; \
  print(''.join(secrets.choice(chars) for _ in range(12)))")

sudo -u "$DEPLOY_USER" "$VENV/bin/python" manage.py shell -c "
from canteen.models import User
if not User.objects.filter(role='admin').exists():
    User.objects.create_superuser(
        username='admin',
        password='$ADMIN_PASS',
        role='admin'
    )
    print('Admin user created.')
else:
    print('Admin user already exists, skipping.')
"

# Save credentials to a file for the technician
CREDS_FILE="$PROJ/ADMIN_CREDENTIALS.txt"
cat > "$CREDS_FILE" << CREDS
TarsierPOS Admin Credentials
=============================
URL:      http://$LAN_IP:8081
Username: admin
Password: $ADMIN_PASS

Keep this file secure. Change the password after first login.
Generated: $(date)
CREDS
chown "$DEPLOY_USER:$DEPLOY_USER" "$CREDS_FILE"
chmod 600 "$CREDS_FILE"

echo "  ✓ Admin credentials saved to $CREDS_FILE"
echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║  Username: admin                     ║"
echo "  ║  Password: $ADMIN_PASS              ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ── 6. collectstatic ──────────────────────────────────────────────────────────
echo "[6/11] Collecting static files..."
sudo -u "$DEPLOY_USER" "$VENV/bin/python" manage.py collectstatic --noinput 2>/dev/null || true

# ── 7. Backups directory ───────────────────────────────────────────────────────
echo "[7/11] Creating backups directory..."
mkdir -p "$PROJ/backups"

# ── 8. Script permissions ──────────────────────────────────────────────────────
echo "[8/11] Setting script permissions..."
chmod +x "$PROJ/health_check.sh" "$PROJ/backup_db.sh"

# ── 9. Systemd services ───────────────────────────────────────────────────────
echo "[9/11] Installing systemd services..."
sed "s|__DEPLOY_USER__|$DEPLOY_USER|g" "$PROJ/tarsierpos.service.template" > "$SERVICE_DIR/tarsierpos.service"

systemctl daemon-reload

# Fix ownership BEFORE starting services
chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$PROJ"
chmod 664 "$PROJ/db.sqlite3" 2>/dev/null || true

systemctl enable tarsierpos.service
systemctl start  tarsierpos.service

# ── 10. Cron watchdog (root crontab) ──────────────────────────────────────────
echo "[10/11] Adding health-check cron job..."
CRON_JOB="*/5 * * * * $PROJ/health_check.sh"
if ! crontab -l 2>/dev/null | grep -qF "$PROJ/health_check.sh"; then
    ( crontab -l 2>/dev/null; echo "$CRON_JOB" ) | crontab -
    echo "  Cron job added."
else
    echo "  Cron job already present — skipping."
fi

# Verify cron was actually installed
if crontab -l 2>/dev/null | grep -q "health_check.sh"; then
    echo "  ✓ Health check cron installed"
else
    echo "  WARNING: Cron installation could not be verified" >&2
fi

# ── 11. Final status ──────────────────────────────────────────────────────────
echo ""
echo "=== Installation complete ==="
echo ""

for svc in tarsierpos; do
    if systemctl is-active --quiet "$svc"; then
        echo "  ✓ $svc  ACTIVE"
    else
        echo "  ✗ $svc  INACTIVE (check: journalctl -u $svc)"
    fi
done

echo ""
echo "  Access URLs:"
echo "    Frontend  : http://${LAN_IP}:8081"
echo "    Backend   : http://${LAN_IP}:9000"
if [ "$TAILSCALE_IP" != "N/A" ]; then
    echo "    Tailscale : http://${TAILSCALE_IP}:8081"
fi
echo ""
