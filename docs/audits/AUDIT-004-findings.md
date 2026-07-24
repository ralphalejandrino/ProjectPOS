# AUDIT-004 — Operations & Automation Inventory

**Date:** 2026-05-16
**Machine:** OptiPlex 3060 (`ralph-OptiPlex-3060`), Ubuntu 24.04.4 LTS
**Repo:** `/home/ralph/TarsierPOS`
**Scope:** Read-only audit. No services touched, nothing installed/modified. State-of-ops snapshot to inform FEATURE-stub filing.

---

## 1. TL;DR

TarsierPOS has **three pieces of intended automation, two of which are silently broken**, and **zero off-machine backup**. The only backup mechanism that actually works is `ExecStopPost=backup_db.sh` on `tarsierpos.service` — it fires *only when the service stops*, keeps 7 rotating copies, and writes them to `/home/ralph/TarsierPOS/backups/` **on the same disk as the database**. The nightly cron backup (`30 23 * * * /home/ralph/pos-modern/backup_db.sh`) points at a **path that does not exist** (the repo was renamed from `pos-modern` to `TarsierPOS`) — it has been a no-op for an unknown period and produces no error anyone sees. The root health watchdog (`*/5 * * * * health_check.sh`) is **permanently failed**: it health-checks the disabled frontend on `:8081` (always down since FIX-047) and tries to restart `tarsierpos-backend` (disabled since FIX-PENDING-13) instead of the canonical `tarsierpos.service` — it has been logging `ALERT: 5 restarts triggered — manual intervention required` every 5 minutes (5,244 such lines) and **cannot recover a real backend outage**.

**Biggest single gap:** no automated off-machine backup of any kind. Every copy of the database lives on `/dev/nvme0n1p2`. A single disk/SSD failure or filesystem corruption is total, unrecoverable data loss for the cafe.

**Highest-impact silent risk:** the nightly backup cron has been dead since the repo rename and **nothing reports the failure**. Combined with backups only otherwise firing on service stop (the service has been up 8h+ with the last backup at 2026-05-16 01:34), the realistic worst case is losing a full day of cafe transactions and not discovering the backup gap until a restore is attempted.

There is **no monitoring, no alerting, no certificate-renewal automation, and no documented shutdown/runbook procedure.** Disk capacity itself is not a concern (144 GB free; the SQLite DB will take decades to become large).

---

## 2. What Runs Automatically Today

### 2.1 systemd services (Phase 1)

| Unit | Enabled | State | Notes |
|---|---|---|---|
| `tarsierpos.service` | **enabled** | active (running, 8h) | **Canonical unit.** `/etc/systemd/system/tarsierpos.service` |
| `tarsierpos-backend.service` | **disabled** | inactive | Superseded per FIX-PENDING-13 ✓ confirmed |
| `tarsierpos-frontend.service` | loaded | exited (no-op) | Deprecated per FIX-047; `ExecStart=/bin/true` |
| `nginx.service` | enabled | active | HTTPS reverse proxy |
| `tailscaled.service` | enabled | active | Tailscale node agent |
| `cups.service`, `cups-browsed`, snap cups | enabled | active | Printing stack |

Canonical unit (`systemctl cat tarsierpos.service`):

```ini
[Unit]
Description=TarsierPOS Gunicorn
After=network.target

[Service]
User=ralph
Group=www-data
WorkingDirectory=/home/ralph/TarsierPOS
ExecStart=/home/ralph/TarsierPOS/venv/bin/gunicorn pos_config.wsgi:application --bind 127.0.0.1:9000 --workers 3 --timeout 60
Restart=always
RestartSec=5
EnvironmentFile=/home/ralph/TarsierPOS/.env
ExecStopPost=/home/ralph/TarsierPOS/backup_db.sh

[Install]
WantedBy=multi-user.target
```

- `Restart=always`, `RestartSec=5s`. **No explicit `StartLimitBurst`/`StartLimitIntervalSec`** — systemd defaults apply (`StartLimitBurst=5`, `StartLimitIntervalUSec=10s`). Because `RestartSec=5s` spaces restarts ~5s apart, a crashloop only accumulates ~2 starts per 10s window and **never trips the 5-in-10s limiter** → effectively *unbounded* restart loop with no cap and no alert. Each restart fires `ExecStopPost` → backup churn.
- `TimeoutStopUSec=1min 30s`. No `tarsierpos.service.d/` drop-ins.
- `After=network.target` only — does **not** wait for Tailscale (already noted in TARSIER_RECOVERY.md).
- `tarsierpos-backend.service` confirmed `disabled` + inactive — **Phase 1.5 satisfied; `tarsierpos.service` is the sole canonical unit.** Confirms FIX-PENDING-13.

### 2.2 systemd timers (Phase 1)

`systemctl list-timers --all`: **18 timers, all OS/distro defaults** (apt-daily, logrotate, fwupd, man-db, sysstat, e2scrub, fstrim, motd, anacron, dpkg-db-backup, tmpfiles-clean). **No TarsierPOS-specific timer exists.** No user-level systemd units relevant to the app (`systemctl --user` shows only device units).

### 2.3 Cron (Phase 2)

**`ralph` crontab (`crontab -l`):**
```cron
*/5 * * * * curl -s http://localhost:18080 > /dev/null 2>&1 || (cd /home/ralph/.openclaw/workspace/mission_control_v2 && nohup npm run start ...)   # unrelated — openclaw mission_control
30 23 * * * /home/ralph/pos-modern/backup_db.sh                                                                                       # ⚠ BROKEN — path does not exist
```
- `/home/ralph/pos-modern` **does not exist** (`ls` → No such file or directory; `readlink -f` returns the literal path, i.e. not a symlink). The repo is `/home/ralph/TarsierPOS`. **The nightly 23:30 backup has been a silent no-op since the repo was renamed.** cron will email the failure to local `ralph` mail spool only — effectively unseen.

**`root` crontab (`sudo crontab -l`):**
```cron
*/5 * * * * /home/ralph/TarsierPOS/health_check.sh   # broken watchdog — see §4
```

- `/etc/crontab`, `/etc/cron.d`, `/etc/cron.{daily,weekly,monthly}`: **stock Ubuntu only** (anacron, e2scrub, sysstat, apt, man-db, logrotate, dpkg). `/etc/anacrontab` stock.
- No Celery / APScheduler / beat in `requirements.txt` or installed packages. **No third-party scheduler.**

### 2.4 Git hooks (Phase 3)

- `.git/hooks/post-commit` (only non-`.sample` hook), 2 lines:
  ```bash
  #!/bin/bash
  git push origin main
  ```
  **Every commit auto-pushes to `git@github.com:ralphalejandrino/TarsierPOS.git`** (`branch.main → origin`). No pre-commit / pre-push / post-merge hooks. No `core.hooksPath` override. `~/.gitconfig` has only `user.name=ralphalejandrino`, `user.email`. **Note:** this pushes *code* only — `.gitignore` excludes `db.sqlite3`, `backups/`, `.env`, `certs/`, `media/`, so the GitHub remote is **not** a data backup.

**Summary — automation that actually works:** (1) `tarsierpos.service` gunicorn with auto-restart, (2) `ExecStopPost` DB backup *on service stop only*, (3) git auto-push of code on commit, (4) OS log rotation for nginx. Everything else intended as automation (nightly backup cron, health watchdog) is broken.

---

## 3. Backup State (Phase 4)

### 3.1 `backup_db.sh` (verbatim, 44 lines, `/home/ralph/TarsierPOS/backup_db.sh`)

```bash
#!/bin/bash
# TarsierPOS — SQLite backup with 7-file rotation
# Called by ExecStopPost on every backend shutdown.
# Must never block — always exit 0.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_SRC="$SCRIPT_DIR/db.sqlite3"
BACKUP_DIR="$SCRIPT_DIR/backups"
LOG="/var/log/tarsierpos-backup.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
FILENAME="db_$(date '+%Y%m%d_%H%M%S').sqlite3"
DEST="$BACKUP_DIR/$FILENAME"

touch "$LOG" 2>/dev/null || LOG="/tmp/tarsierpos-backup.log"
mkdir -p "$BACKUP_DIR" 2>/dev/null || true

if [ ! -f "$DB_SRC" ]; then
    echo "[$TIMESTAMP] ERROR: db.sqlite3 not found at $DB_SRC" >> "$LOG"
    exit 0
fi

if sqlite3 "$DB_SRC" ".backup '$DEST'" 2>/dev/null; then
    INTEGRITY=$(sqlite3 "$DEST" "PRAGMA integrity_check;" 2>/dev/null)
    if [ "$INTEGRITY" = "ok" ]; then
        ls -t "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null || true
        KEPT=$(ls "$BACKUP_DIR"/db_*.sqlite3 2>/dev/null | wc -l)
        echo "[$TIMESTAMP] Backed up to $FILENAME — integrity OK ($KEPT kept)" >> "$LOG"
    else
        echo "[$TIMESTAMP] ERROR: Backup integrity check failed ($INTEGRITY) — deleting $FILENAME" >> "$LOG"
        rm -f "$DEST"
    fi
else
    echo "[$TIMESTAMP] ERROR: sqlite3 backup failed for $DEST" >> "$LOG"
fi
exit 0
```

### 3.2 Assessment

| Aspect | Finding |
|---|---|
| Snapshot method | **Correct.** Uses SQLite `.backup` API (consistent, WAL-safe) + `PRAGMA integrity_check`. **Not** naive `cp` — no WAL-corruption risk. |
| Destination | `/home/ralph/TarsierPOS/backups/` — **local, same disk (`/dev/nvme0n1p2`) as `db.sqlite3`.** |
| Retention | 7 most recent (`tail -n +8 | xargs rm`). Bounded ✓. Current: **7 files, 12 MB total**, each 1,658,880 bytes. |
| Existing backups | All 7 timestamped **2026-05-15 23:41 → 2026-05-16 01:34** — a ~2-hour window (the FIX-PENDING-13 service-bounce night). **No backup in the last ~21 hours** despite `db.sqlite3` modified 2026-05-16 20:27. Backups fire only on service stop; service up since 13:55. |
| Trigger reality | `ExecStopPost` on `tarsierpos.service` only. The intended *nightly* trigger (cron 23:30) is **dead** (§2.3). |
| Backup log | `/var/log/tarsierpos-backup.log` **does not exist**; `/tmp/tarsierpos-backup.log` also absent (and `/tmp` is wiped by `systemd-tmpfiles-clean.timer`). **No audit trail of backups exists** — there is no way to see whether/when backups ran. |
| Restore procedure | **Not documented anywhere.** No README/runbook/comment describes how to restore from a `backups/db_*.sqlite3` file. |
| Read-only verification | ✅ Confirmed: `sqlite3 backups/db_20260516_013457.sqlite3 'SELECT COUNT(*) FROM pos_postransaction;'` → **366**. Latest backup is a valid, queryable database. |
| Cloud tooling | Installed: `gsutil`, `tailscale`, `sqlite3`. **Not installed:** `rclone`, `restic`, `borg`, `aws`, `b2`, `mc`. `~/.config/gcloud` *is* configured (has `application_default_credentials.json`) so a GCS path is technically *available* but **no script uses it**. No `~/.config/rclone|restic`, `~/.borg`, `~/.aws`. |
| **Off-machine copy** | **NONE. No backup is ever transmitted off the OptiPlex automatically.** No `scp`/`rsync`/`rclone`/`gsutil` in any repo script or cron. The GitHub auto-push excludes the DB via `.gitignore`. **Stated plainly: zero off-machine backup exists today.** |

---

## 4. Monitoring / Alerting State (Phase 5)

| Capability | State |
|---|---|
| **Health endpoint** | **Exists.** `pos_config/urls.py` → `api/health/` → `HealthCheckView` (`pos/views.py:45`). Checks DB connection only; returns `{"status":"ok"|"degraded","checks":{...}}`. No disk/printer/cert checks. |
| **Health watchdog** | `health_check.sh` via root cron `*/5`. **BROKEN — see below.** |
| **Application logging** | Django `LOGGING` (`settings.py:211`): single `logging.FileHandler` → `/var/log/tarsierpos-app.log` (env `LOG_FILE`), level `WARNING`; console handler `ERROR`. Plain `FileHandler` — **no rotation**, no `logrotate.d` entry. Currently **796 KB, growing unbounded.** |
| **Nginx logs** | `/var/log/nginx/{access,error}.log`. Rotation: `/etc/logrotate.d/nginx` — daily, keep 14, compressed ✓. access.log 119 KB, error.log 2.6 KB (rotated copies present). Healthy. |
| **System monitoring** | **None.** No `prometheus`, `node_exporter`, `monit`, `netdata`, `grafana`, `glances` (`sysstat` present — stock Ubuntu, not app monitoring). |
| **Alerting** | **None.** `grep` for `smtp/sentry/slack/webhook/discord/telegram` across `pos/` + `pos_config/` returns only a `webhook_url` *config model field* (`models.py:612` — payment-gateway/terminal config, unrelated to failure alerting). No `EMAIL_HOST`, no error reporting. **Nothing fires on failure.** |
| **Printer connectivity** | **Nothing monitors `/dev/usb/lp1`.** No cron, no watchdog check between sales. (USB printer device present in `systemctl --user` device list.) |
| **Service auto-restart** | `tarsierpos.service`: `Restart=always`, `RestartSec=5s`. Default `StartLimitBurst=5`/`StartLimitIntervalSec=10s` — not effectively reached (see §2.1) → effectively unbounded restart, no alert on crashloop. |
| **Journal retention** | `journalctl --disk-usage` → **1.7 GB**. `/etc/systemd/journald.conf` is **all defaults** (no `SystemMaxUse`/`MaxRetentionSec` set) — capped only by systemd default (~4 GB / 10% of fs). Not urgent given 144 GB free. |

### 4.1 The health watchdog is permanently broken

`health_check.sh` (root cron, every 5 min) does three things wrong post-FIX-047/FIX-PENDING-13:

1. Requires **both** `backend_ok` *and* `frontend_ok`. It probes `http://localhost:8081/` for the frontend — but the frontend service is **disabled** (nginx now serves on 443). `frontend_ok` is **always false**.
2. Therefore it *always* takes the failure branch, increments `/tmp/tarsierpos-restart-count`, and after 5 iterations logs `ALERT: 5 restarts triggered — stopping auto-restart. Manual intervention required.` **forever**.
3. When it does attempt recovery it runs `systemctl restart tarsierpos-backend` / `tarsierpos-frontend` — **both disabled units**. It never restarts the canonical `tarsierpos.service`. **Even if the real backend died, this watchdog could not bring it back.**

Evidence from `/var/log/tarsierpos-health.log` (570 KB, root-owned, **no rotation**):
- `5,244` × `ALERT: ... Manual intervention required`
- `48` × `RESTARTED`
- `249` × `OK` (all historical, pre-FIX-047)
- First line `2026-03-19 03:07:40 OK`; every recent line (e.g. `2026-05-16 22:45:01`) is the `ALERT` spam.

The watchdog is **worse than absent**: it provides false assurance, restarts the wrong (dead) units, and grows an unrotated root-owned log by ~one line every 5 minutes indefinitely.

---

## 5. Startup / Shutdown / Certificates (Phase 6)

### 5.1 Power-on / boot

- `tarsierpos.service` is `enabled` (`WantedBy=multi-user.target`), `nginx` enabled, `tailscaled` enabled → cold boot brings the stack up.
- `After=network.target` only; **does not order after Tailscale**. TARSIER_RECOVERY.md already notes UFW `tailscale0` rules are persistent so remote access survives, but the ordering gap is real.
- No `tarsierpos.service.d/` drop-ins. Boot path is clean for the *canonical* unit; the **stale docs still tell the operator to check/enable `tarsierpos-backend.service`** (see §6) — an operator following the reboot checklist would inspect the wrong, disabled unit and could wrongly conclude the system is down.

### 5.2 Clean shutdown / power loss

- Transactions wrap writes in `db_transaction.atomic()` (`pos/views.py:236`, `:1249`, `:1415`). On `systemctl stop`, gunicorn workers get SIGTERM with `TimeoutStopUSec=90s`; an in-flight `atomic()` block either commits or rolls back — no partial transaction. SQLite WAL mode + `busy_timeout=30000` means a hard power-yank loses only the uncommitted transaction and WAL auto-recovers on next open. **Behaviour is acceptable; not a data-integrity gap.**
- **No documented clean-shutdown / end-of-day procedure** (no "close shift → Z report → power down" runbook anywhere in the repo).

### 5.3 Tailscale certificate

- Cert: `/home/ralph/TarsierPOS/certs/ralph-optiplex-3060.example-tailnet.ts.net.crt` (+ `.key`, root-owned).
- `openssl x509 -noout -dates`: `notBefore=May 14 02:50:51 2026 GMT`, **`notAfter=Aug 12 02:50:50 2026 GMT`**. ✅ Matches TARSIER_POS.md's stated 2026-08-12. **~88 days remaining** from 2026-05-16.
- Renewal is **100% manual**, documented only as comments in `tarsierpos-frontend.service` / nginx site: `sudo tailscale cert ralph-optiplex-3060.example-tailnet.ts.net` → move `.crt`/`.key` into `certs/` → `sudo systemctl reload nginx`. **No automation, no reminder, no monitoring of expiry.** When it lapses, HTTPS breaks for the whole cafe and there is nothing to warn beforehand.
- nginx active config `/etc/nginx/sites-enabled/pos` → references the cert path correctly; `default` site also symlinked (stock). Active config matches TARSIER_POS.md intent (proxy `/api/` → `127.0.0.1:9000`, static from `frontend/public`, media alias, auth rate-limit).

### 5.4 Other secrets

- `.env` (600, `/home/ralph/TarsierPOS/.env`, 270 bytes) holds `FERNET_KEY` / `DJANGO_SECRET_KEY` (per `settings.py` usage). **No rotation procedure documented; never rotated.** Excluded from git ✓.

---

## 6. Update / Deployment Posture (Phase 7) — **STALE DOCS**

`TARSIER_POS.md` "Deployment" + `TARSIER_RECOVERY.md` are **out of date and will mislead an operator**:

| Documented step | Reality | Verdict |
|---|---|---|
| `sudo systemctl restart tarsierpos-backend.service` | `tarsierpos-backend.service` is **disabled/inactive**; canonical unit is `tarsierpos.service` | ❌ **Wrong** — restarting the disabled unit does nothing; the live service is untouched |
| Reboot checklist: `systemctl status tarsierpos-backend.service` | Should be `tarsierpos.service` | ❌ Wrong unit |
| Stack = "Django 4.2" | `requirements.txt` → **Django 5.2.14** | ❌ Stale |
| Repo structure lists `tarsierpos-backend.service` as the gunicorn unit, frontend "disabled" | backend now disabled too; `tarsierpos.service` (not in doc) is canonical | ❌ Stale |
| Known issue **FIX-PENDING-02**: "add `backups/` to .gitignore" | `.gitignore` **already contains** `backups/`, `db.sqlite3*`, `.env`, `certs/`, `media/` | ⚠ Already done — issue is stale/closeable |

Other Phase 7 findings:
- **Rollback procedure: none documented.** No "if a migration breaks production, do X" path. No backup-before-migrate convention in any doc or script.
- **Migration safety: none.** No staging step, no test-migrations-first convention. `python manage.py migrate` is run directly on production.
- **Staging environment: none.** It is always git-pull-on-production (single machine; TARSIER_POS.md: "Dev machine only").
- **Remote deploy:** SSH-over-Tailscale is the only path. No push-based/webhook deploy. The `post-commit` hook pushes code to GitHub but **nothing pulls on the OptiPlex** — deployment is fully manual.
- `install.sh` (line ~186/191) enables `tarsierpos-backend tarsierpos-frontend` and installs the root `health_check.sh` cron — i.e. the installer itself encodes the now-superseded service model and the broken watchdog. `install.sh` does **not** set up the nightly backup cron or any cert automation.

---

## 7. Operations Documented vs Implicit (Phase 8)

Docs reviewed: `TARSIER_POS.md`, `TARSIER_RECOVERY.md`, `README.md`, `STATUS.md`, `DIFF_LOG.md`, `docs/FLAG-003-deferred.md`, `.agents/*.md`, script header comments. (No `/home/ralph/TARSIER_*.md` context files on this machine; no `Makefile`.)

| Routine operation | Documented? | Where / Gap |
|---|---|---|
| Post-reboot bring-up checklist | ✅ Yes | `TARSIER_RECOVERY.md` — **but cites wrong unit** (`tarsierpos-backend`) |
| Quick restart (no reboot) | ✅ Yes | `TARSIER_RECOVERY.md` — wrong unit |
| ERR_CONNECTION_REFUSED diagnosis | ✅ Yes | `TARSIER_RECOVERY.md` flowchart (good, but unit names stale) |
| UFW / Tailscale recovery | ✅ Yes | `TARSIER_RECOVERY.md` |
| Cert renewal | ⚠ Partial | Comments in service file / nginx site only; not in main runbook; no cadence/reminder |
| Code deploy / update | ⚠ Stale | `TARSIER_POS.md` — wrong restart command |
| **Daily startup / opening a shift** | ❌ **Absent** | No doc |
| **Daily shutdown / close shift / Z-report / power-down** | ❌ **Absent** | No doc |
| **Weekly / monthly tasks** | ❌ **Absent** | No doc |
| **Emergency: printer down** | ❌ **Absent** | No doc; nothing monitors printer |
| **Emergency: DB corruption / restore from backup** | ❌ **Absent** | No restore procedure anywhere |
| **Emergency: power outage recovery** | ⚠ Partial | Reboot checklist exists; not framed as outage recovery; wrong unit |
| **Backup verification / where backups live** | ❌ **Absent** | Not documented |
| **Rollback after bad migration** | ❌ **Absent** | No doc |
| `.env` / key rotation | ❌ **Absent** | No doc |

**Net:** recovery/networking is reasonably documented (but unit-name-stale); **all routine cafe operations (open, close, Z-report, emergency printer/DB/power) and all data-recovery procedures are undocumented.**

---

## 8. Disk / Resource Growth (Phase 9)

All read-only:

| Item | Value |
|---|---|
| `db.sqlite3` | 1,658,880 B (~1.62 MiB); WAL 90,672 B, SHM 32,768 B |
| Backup history growth signal | **None** — all 7 backups identical size, taken within 2h. No trend derivable. |
| `media/` | **169 MB**, 129 files (product images / QR — largely static) |
| `backups/` | 12 MB, 7 files; oldest `db_20260515_234110`, newest `db_20260516_013457` |
| nginx access.log / error.log | 119 KB / 2.6 KB (rotated, healthy) |
| `/var/log/tarsierpos-app.log` | **796 KB — unrotated, unbounded** |
| `/var/log/tarsierpos-health.log` | **570 KB — unrotated, unbounded, root-owned**, growing ~1 line/5min from the broken watchdog |
| `/var/log/tarsierpos-backup.log` | Does not exist (no backup audit trail) |
| `journalctl --disk-usage` | **1.7 GB** (journald all-defaults, no explicit cap) |
| `df -h /` and `/home` | Same partition `/dev/nvme0n1p2`: **233 G total, 78 G used, 144 G avail, 36 %** |

**Unbounded growth:** only the two TarsierPOS logs (`-app`, `-health`) and journald — all slow, none threatening 144 GB of free space within years. `media/` grows with product catalog (operator-paced, not runaway).

**db.sqlite3 → 1 GB estimate:** AUDIT-003 = 22 real txns / 14 days ≈ 1.6 txn/day. At a generous ~5 KB per transaction (header + line items + variants) → ~8 KB/day → ~3 MB/year. Growing from 1.6 MB to 1 GB ≈ **~300+ years**. Even at 100× volume (busy cafe, ~160 txn/day) → ~0.3 GB/year → **~3 years to add 1 GB**. **Order of magnitude: the SQLite DB is not a disk-fill concern on any realistic horizon.** Disk capacity is a non-issue; the *risk* with the DB is loss (no off-machine copy), not size.

---

## 9. Proposed FEATURE Stubs (identify-only — Ralph assigns numbers)

Conservative scope; post-backlog so severities skew Low/Medium, with two Medium-High exceptions where silent failure causes data loss.

### Cluster A — Backup automation
1. **FEATURE: Fix & re-home the scheduled backup** — Gap: nightly cron points at non-existent `/home/ralph/pos-modern/backup_db.sh`; backups only fire on service stop. **Severity: Medium-High** (silent — data-loss exposure). Dep: none.
2. **FEATURE: Off-machine backup target** — Gap: zero off-host copy; total loss on disk failure. Candidate path: existing `gcloud`/`gsutil` creds, or Tailscale-reachable host. **Severity: Medium-High** (highest-impact gap). Dep: depends on A1 (a working backup to ship).
3. **FEATURE: Backup audit log + restore runbook** — Gap: `tarsierpos-backup.log` never exists; no documented restore-from-`backups/` procedure; no backup-success visibility. **Severity: Medium.** Dep: A1.
4. **FEATURE: Backup retention/scheduling review** — Gap: 7-file rotation tied to service stop is not time-based; consider time-anchored daily + longer retention. **Severity: Low.** Dep: A1.

### Cluster B — Health monitoring & alerting
5. **FEATURE: Repair or replace the health watchdog** — Gap: `health_check.sh` checks disabled `:8081` frontend and restarts disabled `tarsierpos-backend`; permanently in failure state; cannot recover the real service. **Severity: Medium-High** (false assurance + cannot recover backend). Dep: none.
6. **FEATURE: Failure alerting channel** — Gap: nothing notifies anyone on backend down / backup fail / disk full / printer down. **Severity: Medium.** Dep: B5 (a working health signal to alert on).
7. **FEATURE: Log rotation for app/health/backup logs** — Gap: `tarsierpos-app.log`, `tarsierpos-health.log` unrotated, unbounded; no `logrotate.d` entry. **Severity: Low.** Dep: none.

### Cluster C — Startup / shutdown protocols & documentation
8. **FEATURE: Cafe daily-ops runbook** — Gap: no documented open-shift / close-shift / Z-report / power-down / printer-down / DB-corruption / power-outage procedures. **Severity: Medium.** Dep: none.
9. **FEATURE: Boot ordering review (Tailscale-before-app)** — Gap: `After=network.target` only. **Severity: Low.** Dep: none.

### Cluster D — Update / deployment
10. **FEATURE: Correct & re-verify deployment + recovery docs** — Gap: `TARSIER_POS.md`/`TARSIER_RECOVERY.md`/`README.md`/`install.sh` reference disabled `tarsierpos-backend.service`, "Django 4.2", and a now-redundant FIX-PENDING-02. **Severity: Medium** (an operator following docs touches the wrong unit during an incident). Dep: none.
11. **FEATURE: Backup-before-migrate + rollback procedure** — Gap: no migration-safety or rollback path documented; migrations run live on prod. **Severity: Medium.** Dep: A1 (reliable pre-migrate snapshot).

### Cluster E — Certificate renewal
12. **FEATURE: Tailscale cert renewal automation + expiry monitoring** — Gap: 90-day cert (expires **2026-08-12**), fully manual, no reminder/monitoring; lapse breaks cafe HTTPS silently. **Severity: Medium.** Dep: B6 ideally (alert on approaching expiry).

### Cluster F — Operations runbook documentation
13. **FEATURE: Single consolidated ops runbook** — Gap: ops knowledge scattered across stale `.md`s + script comments; no canonical, current operations doc. **Severity: Low.** Dep: C8, D10 (absorbs them).

---

## 10. Open Observations (not tickets)

- **`tarsierpos.service` has no effective crashloop ceiling.** With `Restart=always` + `RestartSec=5s`, restarts are spaced ~5s apart and never hit the default `StartLimitBurst=5`/`10s` window — a persistently crashing gunicorn restarts **forever**, silently consuming CPU, with no alert and (because each stop fires `ExecStopPost`) rapid backup churn that rotates good backups out within minutes (this is exactly what produced the 7 backups clustered in a 2-hour window on the FIX-PENDING-13 night).
- **The two broken automations interact badly.** A real backend outage would be (a) not recovered by the watchdog (wrong/disabled unit), (b) not alerted (no alerting), and (c) the only fresh backup would be whatever `ExecStopPost` wrote when the service last stopped — which during a crashloop is rapidly churned. The system can lose a day of sales and the operator's first signal is a customer complaint.
- **`/tmp` for fallback logs is unsafe** — `systemd-tmpfiles-clean.timer` is active and ran ~2h before this audit; any `/tmp/tarsierpos-backup.log` fallback is periodically erased.
- **gcloud credentials already exist** (`~/.config/gcloud/application_default_credentials.json`) and `gsutil` is installed — an off-machine GCS backup is low-friction to stand up, but is **currently entirely unused** by TarsierPOS.
- **`install.sh` encodes the obsolete model** — it enables `tarsierpos-backend`/`tarsierpos-frontend` and installs the broken watchdog cron. Any future re-install/USB-image deploy would resurrect both broken automations. The installer, not just the docs, has drifted from reality.
- **GitHub auto-push is code-only.** `.gitignore` correctly excludes data; do not mistake the `post-commit` push for a backup. It does mean uncommitted *code* changes on the OptiPlex are the only un-backed-up code — generally fine given commit discipline, worth noting for completeness.
- **Health endpoint is shallow.** `/api/health/` only does `connection.ensure_connection()` — it would report `ok` even if the printer is down, disk is full, or the cert is expiring. Any future monitoring should not rely on it alone.

---
*End of AUDIT-004 findings. Audit-only: no services, units, cron entries, or files were modified.*
