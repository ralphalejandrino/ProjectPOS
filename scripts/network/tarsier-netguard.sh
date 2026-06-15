#!/bin/bash
# tarsier-netguard — privileged WiFi manager for TarsierPOS (FEATURE-039)
set -euo pipefail

STATE_DIR="/var/lib/tarsierpos-network"
STATE_FILE="$STATE_DIR/state.json"

die() { echo "$1" >&2; exit 1; }
mkdir -p "$STATE_DIR"

# Detect active WiFi interface
wifi_iface() {
  nmcli -t -f DEVICE,TYPE device status 2>/dev/null \
    | awk -F: '$2=="wifi"{print $1; exit}'
}

case "${1:-}" in

  apply)
    ssid="${2:-}"
    [[ -n "$ssid" ]] || die "apply requires SSID"
    password=$(cat)

    # Save current active WiFi connection name for revert
    prev=$(nmcli -t -f NAME,TYPE con show --active 2>/dev/null \
           | awk -F: '$2=="802-11-wireless"{print $1; exit}')

    python3 -c "
import json, sys
json.dump({'status':'pending','prev_connection':sys.argv[1]},
          open('$STATE_FILE','w'))
" "$prev"

    # Start 60s auto-revert BEFORE connecting
    systemd-run --on-active=60 --unit=tarsierpos-netrevert \
      /usr/local/sbin/tarsier-netguard check 2>/dev/null || true

    nmcli device wifi rescan 2>/dev/null || true
    sleep 3
    iface=$(wifi_iface)
    if [[ -n "$password" ]]; then
      nmcli device wifi connect "$ssid" password "$password" ifname "$iface" || {
        /usr/local/sbin/tarsier-netguard check
        die "failed to connect to $ssid"
      }
    else
      nmcli device wifi connect "$ssid" ifname "$iface" || {
        /usr/local/sbin/tarsier-netguard check
        die "failed to connect to $ssid"
      }
    fi
    echo "connected to $ssid — confirm within 60s or will revert"
    ;;

  confirm)
    systemctl stop tarsierpos-netrevert.service 2>/dev/null || true
    echo '{"status":"confirmed"}' > "$STATE_FILE"
    echo "confirmed"
    ;;

  check)
    [[ -f "$STATE_FILE" ]] || exit 0
    status=$(python3 -c \
      "import json; print(json.load(open('$STATE_FILE')).get('status',''))" \
      2>/dev/null || echo "")
    [[ "$status" == "pending" ]] || exit 0
    prev=$(python3 -c \
      "import json; print(json.load(open('$STATE_FILE')).get('prev_connection',''))" \
      2>/dev/null || echo "")
    [[ -n "$prev" ]] && nmcli connection up "$prev" 2>/dev/null || true
    rm -f "$STATE_FILE"
    echo "reverted to $prev"
    ;;

  status)
    nmcli -t -f NAME,TYPE,DEVICE,STATE con show --active 2>/dev/null
    [[ -f "$STATE_FILE" ]] && cat "$STATE_FILE" || echo "{}"
    ;;

  *) die "usage: tarsier-netguard {apply <ssid>|confirm|check|status}" ;;
esac
