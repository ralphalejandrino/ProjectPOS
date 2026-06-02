# TarsierPOS — Operations Runbook

**The single source of truth for running the café POS.** Written for whoever is
operating the box (Gio, a café manager, or Ralph). Follow the section you need;
no prior context assumed.

- For deep install/feature internals (GCS backup setup, key rotation, systemd
  drop-in mechanics), see **`docs/OPS.md`**.
- For the one-page cashier card (start/end of day, what to do when something
  breaks), see **`docs/CASHIER-QUICKREF.md`**.

Throughout, `$TARSIERPOS_DIR` is the repo root. On the café box it is
`/home/ralph/TarsierPOS`; export it once per shell if a command uses it:

```sh
export TARSIERPOS_DIR=$HOME/TarsierPOS
```

The backend systemd unit is `tarsierpos` on the dev box and may be
`tarsierpos-backend` elsewhere — `${TARSIERPOS_SERVICE}` from `.env` resolves it.

---

## 1. Fresh-box install checklist

Run top to bottom on a brand-new machine.

```sh
# 1. System deps
sudo apt update
sudo apt install -y python3-venv python3-pip nginx git

# 2. Clone the repo (path is up to you; /opt/tarsierpos or a home dir both work)
git clone <repo-url> "$HOME/TarsierPOS"
cd "$HOME/TarsierPOS"
export TARSIERPOS_DIR="$PWD"

# 3. Python environment
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 4. Per-box config — create .env (never committed). At minimum:
#    DJANGO_SECRET_KEY, FERNET_KEY, TAILSCALE_HOSTNAME, TARSIERPOS_SERVICE
#    (see docs/OPS.md "Key rotation" for how to generate the keys).

# 5. nginx must be able to read the install tree.  <-- FIX-PENDING-18
#    On production boxes the app dir is group-owned by `tarsier`; nginx runs as
#    www-data and MUST be in that group or static assets 403 and the POS shows
#    blank. install-ops-units.sh does this automatically (idempotent; skips
#    boxes with no `tarsier` group, e.g. the dev OptiPlex). To do it by hand:
sudo usermod -aG tarsier www-data    # then restart nginx for it to take effect

# 6. Install systemd backend service + drop-ins (see docs/OPS.md for the
#    --preload, boot-ordering, and crashloop-limit drop-ins), then:
sudo systemctl enable --now tarsierpos
sudo systemctl enable --now nginx

# 7. Database + static + seed
venv/bin/python manage.py migrate          # use safe_migrate on a box with data
venv/bin/python manage.py collectstatic --noinput
# (seed initial data per the project's seed command if this is a clean DB)

# 8. Ops timers (cert renewal, backups, daily health) — portable, no manual cp:
sudo TARSIERPOS_DIR="$TARSIERPOS_DIR" bash "$TARSIERPOS_DIR/scripts/install-ops-units.sh"
systemctl list-timers | grep tarsierpos
```

Verify the box is live before declaring done:

```sh
sudo systemctl status tarsierpos nginx       # both active (running)
sudo ss -tlnp | grep -E '443|9000'           # both ports listening
tailscale status                             # Tailscale up
curl -k https://localhost/ -o /dev/null -w "%{http_code}\n"   # 200
```

---

## 2. Daily startup procedure

1. Power on the POS box. Both `tarsierpos` and `nginx` are `enabled` and start
   on boot — no manual start needed.
2. Wait for the kiosk browser to load. Confirm the **quick-login grid** appears
   at `https://localhost/` (the cashier avatar tiles). If it does not, see
   *Quick-login grid missing* under Common Incidents — it is almost always a
   loopback-vs-hostname issue.
3. The opening cashier logs in and **opens the shift** from the POS screen.

---

## 3. Daily shutdown / Z-report procedure

1. **Finalize the Z-report** for the day from the POS (Z report screen). Confirm
   a `ZReport` row is created — it appears in the report history.
2. **Close the shift.**
3. Confirm the nightly **backup ran**: the local snapshot timer fires at 23:30.
   Check with `tail -3 "$TARSIERPOS_DIR/logs/backup.log"` — the last line shows
   `(N kept, GFS 7d/4w)`. The off-site (USB + GCS) mirror runs at 23:45.
4. The box can be left on overnight (timers are time-anchored) or shut down —
   `Persistent=true` timers catch up missed runs on next boot.

---

## 4. Common incidents

**Printer not responding.** Check the printer transport config (USB/serial vs
network) in Settings, confirm the cable/power, then restart the backend:
`sudo systemctl restart "${TARSIERPOS_SERVICE:-tarsierpos}"`. Re-print the last
receipt to test.

**POS not loading (blank or error page).** Check, in order:
```sh
sudo systemctl status tarsierpos        # backend up?
sudo systemctl status nginx             # web server up?
curl -k https://localhost/ -o /dev/null -w "%{http_code}\n"   # expect 200
```
If nginx is up but assets 403 / page is blank, www-data is likely not in the
`tarsier` group — see step 5 of the fresh-box checklist.

**Quick-login grid missing.** Open the POS via `https://localhost/` (loopback),
not the machine hostname or LAN IP. The grid renders for the loopback origin;
hitting it by hostname can suppress it.

**Database locked.** SQLite `busy_timeout` is 30s — a lock under load clears
itself. Wait and retry the action. If it persists, restart the backend.

**Service keeps crashing / restart loop.** The crashloop ceiling is 10 starts
per 600s (`limits.conf`). If the unit enters `failed` after hitting it:
```sh
sudo journalctl -u tarsierpos -n 100 --no-pager   # find the crash cause
sudo systemctl reset-failed tarsierpos
sudo systemctl start tarsierpos
```

---

## 5. Backup & recovery

**Schedule** (all time-anchored, `Persistent=true`):

| Job                         | When        | Unit                              |
|-----------------------------|-------------|-----------------------------------|
| Local snapshot + GFS prune  | daily 23:30 | `tarsierpos-backup-local.timer`   |
| Off-site mirror (USB + GCS) | daily 23:45 | `tarsierpos-backup-offsite.timer` |
| GCS retry flush             | hourly      | `tarsierpos-backup-retry.timer`   |

**Retention** is grandfather-father-son: newest snapshot per day for the last 7
days, plus newest per ISO week for the last 4 weeks. `pre-migrate-*` snapshots
are kept independently and never pruned.

**Before any migration**, always use `safe_migrate` (never bare `migrate`) — it
snapshots the DB first and prints rollback steps on failure:
```sh
cd "$TARSIERPOS_DIR"
venv/bin/python manage.py safe_migrate            # snapshot + migrate
```

**Restore from backup:**
```sh
sudo systemctl stop "${TARSIERPOS_SERVICE:-tarsierpos}"
cp "$TARSIERPOS_DIR/backups/<snapshot>.sqlite3" "$TARSIERPOS_DIR/db.sqlite3"
sudo systemctl start "${TARSIERPOS_SERVICE:-tarsierpos}"
```
Use a `pre-migrate-<timestamp>.sqlite3` after a bad migration, or the newest
`db_<timestamp>.sqlite3` otherwise. Full GCS/USB detail is in `docs/OPS.md`.

---

## 6. Cert renewal

The Tailscale cert is renewed automatically by `tarsierpos-cert-renew.timer`
(FEATURE-018). The current cert **expires 2026-08-12**; renewal runs ahead of
that and logs a WARNING if a renewed cert is still < 30 days from expiry.

If automation fails, renew manually:
```sh
sudo "$TARSIERPOS_DIR/scripts/cert/renew-cert.sh"
sudo systemctl reload nginx
```

---

## 7. Log locations

- **Backend (gunicorn) logs → systemd journal** (canonical):
  ```sh
  journalctl -u tarsierpos -f                  # follow live
  journalctl -u tarsierpos -n 200 --no-pager   # recent
  ```
  There is **no `tarsierpos-app.log`** file — per ISSUE-093 the journal is the
  single canonical log; do not look for or expect a separate app logfile.
- **Daily health summary:** `journalctl -t tarsierpos-health --since today`.
- **Backup audit:** `$TARSIERPOS_DIR/logs/backup.log` and
  `$TARSIERPOS_DIR/logs/backup-audit.log`.

---

## 8. Remote access (Tailscale)

- **SSH:** `ssh ralph@pos-01` over Tailscale.
- **Remote ops view (phone-friendly, read-only):** `https://<hostname>/remote/`
  — shows the open shift, today's gross, last backup, and recent transactions.
- **Clean backend log over SSH:** `"$TARSIERPOS_DIR/scripts/ops/logs.sh" -f`.

---

## Development

Run before committing any frontend (HTML/CSS) change:

```sh
python3 scripts/check_dead_classes.py    # FEATURE-042
```

`styles.css` is a *purged* Tailwind build and there is no build step wired into
the workflow, so Tailwind's content scan never re-runs — a class typed into HTML
after the last build silently renders unstyled. This script is the manual
substitute: it reports class tokens used in HTML that resolve to no CSS rule
(ignoring page-local `<style>` blocks, JS hooks, and `ingredients.html`, which
uses the Tailwind CDN). Exits non-zero when dead classes are found so it can be
wired into a pre-commit hook.

Always run `check_dead_classes.py` before committing a frontend change. A known
residual of state-variant utilities (`dark:` / `hover:` / `focus:` / `disabled:`)
remains accepted — they have no runtime impact (dark mode uses `.dark` overrides,
not `dark:*` utilities). New **static** utilities flagged by the checker must be
patched into `shared-styles.css` (see the `dead-class patch` block) before commit.
