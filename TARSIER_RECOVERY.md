# TarsierPOS — Recovery & Reboot Protocol
**Version:** 1.0 | **Date:** 2026-05-12
**Add this section to TARSIER_POS.md under a new "Recovery" heading.**

---

## Auto-Start (One-Time Setup — Already Done)

Both services are enabled to start on boot:
```bash
sudo systemctl enable tarsierpos.service
sudo systemctl enable nginx
```
gunicorn has `Restart=always` with `RestartSec=5` — it auto-restarts on crash without a reboot.

---

## After Any OptiPlex Reboot — Full Checklist

Run in this exact order:

```bash
# 1. Confirm both services came up
sudo systemctl status tarsierpos.service
sudo systemctl status nginx

# 2. Confirm ports are listening
sudo ss -tlnp | grep -E '443|9000'

# 3. Confirm Tailscale is up
tailscale status

# 4. Self-test from OptiPlex
curl -k https://localhost/ -o /dev/null -w "%{http_code}\n"
```

All four must pass before declaring the system live.

---

## Quick Restart (No Reboot)

```bash
sudo systemctl restart tarsierpos.service
sudo systemctl reload nginx
```

---

## Diagnosis Flowchart

**Browser shows ERR_CONNECTION_REFUSED**

```
1. Is gunicorn running?
   sudo systemctl status tarsierpos.service
   → Not running: sudo systemctl restart tarsierpos.service

2. Is nginx running and listening on 443?
   sudo ss -tlnp | grep 443
   → Not listening: sudo systemctl restart nginx

3. Can OptiPlex reach itself?
   curl -k https://localhost/ -o /dev/null -w "%{http_code}\n"
   → Not 200: check nginx config → sudo nginx -t

4. Is Tailscale up?
   tailscale status
   → Not active: sudo tailscale up

5. Can the laptop reach port 443?
   (Run on Windows laptop PowerShell)
   Test-NetConnection -ComputerName 100.100.100.100 -Port 443
   → TcpTestSucceeded: False → UFW issue (see below)
   → TcpTestSucceeded: True → Browser cache issue (see below)
```

**UFW fix (if TCP test fails from laptop):**
```bash
sudo ufw allow in on tailscale0 to any port 443
sudo ufw allow in on tailscale0 to any port 80
sudo ufw reload
```

**Browser cache fix (if TCP works but browser refuses):**
- Incognito window first to confirm site is live
- Main window: chrome://net-internals/#hsts → Delete domain → type `100.100.100.100`
- Or: Ctrl+Shift+Delete → Clear cached images and files

---

## Service File Reference

`/etc/systemd/system/tarsierpos.service`
- `Restart=always` — auto-restarts on crash
- `RestartSec=5` — 5s delay between restart attempts
- `After=network.target` — waits for network before starting
- `ExecStopPost=backup_db.sh` — DB backup runs on every stop

**Note:** `After=network.target` does NOT guarantee Tailscale is up.
If Tailscale takes longer than nginx to start after reboot, the UFW
tailscale0 rules will still apply — they are persistent via ufw.

---

## Access URLs
| From | URL |
|---|---|
| Laptop (red-eye) via Tailscale | https://100.100.100.100/ |
| OptiPlex locally | https://localhost/ |

---

## Verified Working State (2026-05-12)
- tarsierpos.service: enabled, Restart=always ✓
- nginx: enabled ✓
- UFW: 443/80 allowed on tailscale0 ✓
- Tailscale: active, direct connection to red-eye ✓
