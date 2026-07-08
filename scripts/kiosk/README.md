# Kiosk self-heal — no more manual reboots on a white screen

The PROD kiosk went to a **full white screen** during cash entry and needed a
**manual reboot**. The app-side trigger (on-screen-keyboard input swallowing
taps) is fixed, but two facts remained:

- a low-end GPU can still crash the browser compositor for other reasons, and
- **nothing on the box recovered a blanked browser** — the FEATURE-045
  watchdog only heals WiFi/Tailscale and never reboots.

This directory closes that gap with **two composable layers**. Together they
mean a white screen either **doesn't happen** or **recovers itself in seconds**
with no operator and no reboot.

## Layer 1 — `kiosk-run.sh` (launcher): remove the cause + self-heal crashes
- **Software rendering** (`--disable-gpu`): a web POS needs no GPU, so taking the
  GPU out of the path removes the compositor-crash class that produces the white
  screen on flaky panels/drivers.
- **Relaunch loop**: Chromium runs inside `while true`; any exit (crash, OOM, or
  a watchdog kill) relaunches a fresh POS login in ~2s.

## Layer 2 — `tarsier-kiosk-watchdog.sh` (watchdog): catch a blank *live* tab
The launcher can't see the case where the browser process is alive but the tab
has gone uniform white/black (renderer crash). The watchdog screenshots the
screen every 15s and reads its normalized standard deviation:

| screen | stddev | verdict |
|---|---|---|
| crash white / black | `0.0000` | blank |
| healthy POS (lightest = dashboard) | `0.13` | healthy |
| healthy POS (cashier) | `0.29` | healthy |

Threshold `0.012` sits ~10× below the lightest healthy screen, so a normal
(even mostly-white) POS never false-trips. After **3 consecutive** blank cycles
(~45s of continuous dead screen) it kills Chromium; the launcher relaunches.
**Never reboots.** With no X display / no screenshot tool it logs and does
nothing (never a false kill).

## Install (on the client box, as posadmin/root)

1. **Dependencies** (for the watchdog's screenshot + analysis):
   ```bash
   sudo apt-get install -y imagemagick        # provides `import` + `convert`
   ```
2. **Launcher.** `kiosk-run.sh` mirrors the proven pos-01 flags (incl.
   `--ignore-certificate-errors`, snap chromium path) plus `--disable-gpu`.

   - **New box:** install it and point the kiosk X startup at it:
     ```bash
     sudo install -m 0755 -o root -g root scripts/kiosk/kiosk-run.sh /usr/local/bin/kiosk-run.sh
     ```
     In the autologin kiosk user's `~/.xinitrc`, keep the display setup (xrandr /
     setxkbmap / unclutter) and replace the single `chromium --kiosk … https://localhost`
     line with `exec /usr/local/bin/kiosk-run.sh`.

   - **Existing box that already has its own `while true; do chromium …; done`
     loop** (this is how pos-01 is set up): don't swap in the script — just
     add `--disable-gpu` to the existing chromium line, which keeps every
     box-specific flag you already rely on:
     ```bash
     cp ~/.xinitrc ~/.xinitrc.bak-$(date +%Y%m%d-%H%M%S)
     sed -i 's|chromium --kiosk|chromium --disable-gpu --kiosk|' ~/.xinitrc
     ```
     Then install the watchdog (step 3) and reboot.
3. **Watchdog** — root-owned copy + timer:
   ```bash
   sudo install -m 0755 -o root -g root scripts/kiosk/tarsier-kiosk-watchdog.sh /usr/local/sbin/tarsier-kiosk-watchdog
   sudo install -m 0644 scripts/systemd/tarsier-kiosk-watchdog.service /etc/systemd/system/
   sudo install -m 0644 scripts/systemd/tarsier-kiosk-watchdog.timer   /etc/systemd/system/
   # Adjust XAUTHORITY in the .service if the kiosk user isn't posadmin.
   sudo systemctl daemon-reload
   sudo systemctl enable --now tarsier-kiosk-watchdog.timer
   ```

## Verify
- `chrome://gpu` shows software rendering (GPU lines "Software only").
- Watchdog heartbeat: `tail -f /var/log/tarsier/kiosk-watchdog.log` → `OK stddev=…`.
- Recovery drill (off-hours): `sudo pkill -STOP -f chrom` to freeze the tab, or
  cover the render — after ~45s the log shows `RECOVER killed Chromium …` and the
  POS returns on its own. Dry-run first with `KIOSK_WD_DRY_RUN=1`.

## Honest scope
Layer 1 makes the observed white-screen cause (GPU compositor) essentially
impossible and self-heals any browser death. Layer 2 catches a blank live tab
from any cause and recovers it. Neither can fix a dead **X server** or a kernel
hang — those are rarer and would still need the box power-cycled; if that ever
shows up, the next step is a display-manager (`kiosk.service`) restart or a
hardware watchdog, added the same way.
