"""FEATURE-039: thin wrapper around the privileged tarsier-netguard binary.

Django runs as the app user and must never touch networking directly. All
mutating operations go through `sudo tarsier-netguard ...` (scoped via
/etc/sudoers.d/tarsierpos-network); reads come from the world-readable state
file and read-only `nmcli` queries. Kept deliberately thin so views stay simple
and the subprocess boundary is easy to mock in tests.
"""
import json
import subprocess

from django.conf import settings

_TIMEOUT = 20  # seconds — generous; activation itself happens async in the script


def get_state():
    """Return the current netguard state dict, or {} if no change is on record."""
    try:
        with open(settings.NETWORK_STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def current_wifi():
    """Read-only: the active WiFi SSID + device. No privileges required."""
    try:
        out = subprocess.run(
            ['nmcli', '-t', '-f', 'NAME,TYPE,DEVICE', 'connection', 'show', '--active'],
            capture_output=True, text=True, timeout=_TIMEOUT,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {'ssid': None, 'device': None}
    for line in out.splitlines():
        parts = line.split(':')
        if len(parts) >= 3 and parts[1] == '802-11-wireless':
            return {'ssid': parts[0], 'device': parts[2]}
    return {'ssid': None, 'device': None}


def apply(ssid, password):
    """Apply a new WiFi profile via the privileged binary. Password goes on stdin
    (never argv) so it stays out of `ps` and the sudo audit log. Returns
    (ok: bool, message: str)."""
    try:
        proc = subprocess.run(
            ['sudo', '-n', settings.NETGUARD_BIN, 'apply', ssid],
            input=(password or ''), capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, 'network apply timed out'
    except OSError as e:
        return False, f'could not run netguard: {e}'
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or 'apply failed').strip()
    return True, proc.stdout.strip()


def confirm():
    """Confirm the pending change (explicit admin success signal). Returns
    (ok: bool, message: str)."""
    try:
        proc = subprocess.run(
            ['sudo', '-n', settings.NETGUARD_BIN, 'confirm'],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, 'network confirm timed out'
    except OSError as e:
        return False, f'could not run netguard: {e}'
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or 'confirm failed').strip()
    return True, proc.stdout.strip()
