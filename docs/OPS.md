# Operations

## Logs

### Django application log (WARNING + ERROR level)
Path: `/var/log/tarsierpos-app.log`
Tail: `sudo tail -f /var/log/tarsierpos-app.log`
Rotation: handled by `/etc/logrotate.d/tarsierpos` (daily, keep 14, compressed)

### gunicorn stdout/stderr (request-level activity, worker boot, errors)
Source: systemd journal
Tail: `sudo journalctl -u tarsierpos.service -f`
Recent: `sudo journalctl -u tarsierpos.service -n 200 --no-pager`
Rotation: handled by systemd-journald (defaults apply unless tuned separately)

### Old health watchdog log (disabled, may not have recent writes)
Path: `/var/log/tarsierpos-health.log`
Rotation: handled by `/etc/logrotate.d/tarsierpos` (defensive — kept in case
FEATURE-020's replacement uses the same path)

### Install log rotation
```sh
sudo install -m 0644 -o root -g root scripts/logrotate/tarsierpos /etc/logrotate.d/tarsierpos
sudo logrotate --debug /etc/logrotate.d/tarsierpos   # dry-run, no changes
```

## Post-commit hook setup

The repo ships a versioned post-commit hook that auto-pushes to `origin/main`
and auto-restarts `tarsierpos.service` (clearing stale bytecode first) so new
`.py` files take effect without a manual restart.

### Install

```sh
# 1. Sudoers rule for passwordless restart (validate first!)
sudo visudo -c -f scripts/sudoers/tarsierpos-restart   # must print "parsed OK"
sudo install -m 0440 -o root -g root scripts/sudoers/tarsierpos-restart /etc/sudoers.d/tarsierpos-restart

# 2. Post-commit hook
cp scripts/post-commit-hook .git/hooks/post-commit
chmod +x .git/hooks/post-commit
```

### Verify

```sh
# Passwordless restart works (no prompt, exit 0)
sudo -n /bin/systemctl restart tarsierpos.service
# Service is up
systemctl status tarsierpos.service | head -5
```

After any commit, `systemctl status tarsierpos.service` should show an
`Active: active (running) since ...` timestamp within seconds of the commit.

## FLAG-067: gunicorn --preload

A systemd drop-in adds `--preload` to gunicorn so the WSGI app is imported in
the master process before forking workers. Import errors then crash the unit at
service-start time instead of on the first request after a worker death.

### Install

```sh
sudo mkdir -p /etc/systemd/system/tarsierpos.service.d/
sudo install -m 0644 -o root -g root \
  scripts/systemd/tarsierpos.service.d/preload.conf \
  /etc/systemd/system/tarsierpos.service.d/preload.conf
sudo systemd-analyze verify tarsierpos.service   # no warnings expected
sudo systemctl daemon-reload
sudo systemctl restart tarsierpos.service
```

Verify the drop-in is merged with `systemctl cat tarsierpos.service` (the
effective `ExecStart` should end with `--preload`).

### Revert

```sh
sudo rm /etc/systemd/system/tarsierpos.service.d/preload.conf
sudo systemctl daemon-reload
sudo systemctl restart tarsierpos.service
```

## FEATURE-022: Boot ordering

A systemd drop-in delays `tarsierpos.service` until `tailscaled.service` and
`network-online.target` are reached, so gunicorn does not start before the box
has Tailscale up and a usable network. The stock unit only orders after
`network.target` (link configured, not necessarily online). `Wants=` is
required because `network-online.target` is not pulled into the boot
transaction by default — without it the `After=` would wait on a target that is
never activated.

### Install

```sh
sudo mkdir -p /etc/systemd/system/tarsierpos.service.d/
sudo install -m 0644 -o root -g root \
  scripts/systemd/tarsierpos.service.d/ordering.conf \
  /etc/systemd/system/tarsierpos.service.d/ordering.conf
sudo systemd-analyze verify tarsierpos.service   # no warnings expected
sudo systemctl daemon-reload
```

Verify the drop-in is merged with `systemctl cat tarsierpos.service` (the
effective `[Unit]` section should list `tailscaled.service` and
`network-online.target` in `After=`). `systemd-analyze critical-chain
tarsierpos.service` should show both in the chain. The ordering only takes
effect at the next reboot; no restart is needed to apply the change.

### Revert

```sh
sudo rm /etc/systemd/system/tarsierpos.service.d/ordering.conf
sudo systemctl daemon-reload
```

## FEATURE-017: Off-site backup

`scripts/backup/offsite-backup.sh` copies the latest local snapshot
(`backups/db_*.sqlite3`, produced by FIX-PENDING-15) to off-site targets. A
systemd timer runs it daily at 23:45. Sub-commit (a) ships the **local USB**
target only: it copies to `/mnt/backup/` when that path is a mountpoint and logs
a `local SKIP` otherwise (the OptiPlex has no USB SSD). GCS upload (b) and the
offline queue + hourly flush (c) land in later sub-commits and are currently
no-op comments in the script. All actions append to
`/home/ralph/TarsierPOS/logs/backup-audit.log`.

### Install

```sh
sudo install -m 0644 -o root -g root \
  scripts/systemd/tarsierpos-backup-offsite.service /etc/systemd/system/
sudo install -m 0644 -o root -g root \
  scripts/systemd/tarsierpos-backup-offsite.timer /etc/systemd/system/
sudo systemd-analyze verify \
  tarsierpos-backup-offsite.service tarsierpos-backup-offsite.timer
sudo systemctl daemon-reload
sudo systemctl enable --now tarsierpos-backup-offsite.timer
systemctl list-timers tarsierpos-backup-offsite.timer
```

### Manual run

```sh
sudo systemctl start tarsierpos-backup-offsite.service
systemctl status tarsierpos-backup-offsite.service --no-pager
tail -10 /home/ralph/TarsierPOS/logs/backup-audit.log
```

On a host without `/mnt/backup` mounted, expect `begin ...`, `local SKIP
/mnt/backup not a mountpoint`, `end`. To exercise the mounted branch, bind-mount
a writable dir at `/mnt/backup` (owned by ralph) and re-run; expect `local OK
dest=/mnt/backup/db_<ts>.sqlite3`.

### Revert

```sh
sudo systemctl disable --now tarsierpos-backup-offsite.timer
sudo rm /etc/systemd/system/tarsierpos-backup-offsite.{service,timer}
sudo systemctl daemon-reload
```

## GCS off-site upload (FEATURE-017(b))

The offsite backup script also uploads each snapshot to Google Cloud Storage
when `GCS_DEST` is set. Auth is a service-account JSON key on disk; the SA is
scoped to one bucket only (no project-level roles). 3-attempt retry with
exponential backoff (10s, 20s, 40s); on full failure the script logs `gcs
ABORT` and (sub-commit c) will enqueue the file for later retry.

### One-time GCP setup (org-less project `pos-backup-project`)

```sh
gcloud projects create pos-backup-project --name="TarsierPOS Backup"
gcloud billing projects link pos-backup-project \
  --billing-account=0147A3-995DE3-0F35C4
gcloud config set project pos-backup-project
gcloud services enable storage.googleapis.com iam.googleapis.com \
  --project=pos-backup-project

# Bucket: asia-southeast1, Standard, uniform BLA, versioning on, 90d delete
gsutil mb -p pos-backup-project -c STANDARD -l asia-southeast1 -b on \
  gs://tarsierpos-backups-rma/
gsutil versioning set on gs://tarsierpos-backups-rma/
cat > /tmp/lifecycle.json <<'EOF'
{"lifecycle":{"rule":[{"action":{"type":"Delete"},"condition":{"age":90}}]}}
EOF
gsutil lifecycle set /tmp/lifecycle.json gs://tarsierpos-backups-rma/
rm /tmp/lifecycle.json

# Service account, bucket-scoped objectAdmin (NOT project-level)
gcloud iam service-accounts create tarsierpos-backup \
  --display-name="TarsierPOS off-site backup" \
  --project=pos-backup-project
SA_EMAIL=tarsierpos-backup@pos-backup-project.iam.gserviceaccount.com
gsutil iam ch serviceAccount:${SA_EMAIL}:objectAdmin \
  gs://tarsierpos-backups-rma/
# Verify project-level policy for the SA is empty:
gcloud projects get-iam-policy pos-backup-project \
  --flatten="bindings[].members" --filter="bindings.members:${SA_EMAIL}"
```

### Per-machine key + drop-in install

```sh
gcloud iam service-accounts keys create /tmp/gcs-backup-sa.json \
  --iam-account=${SA_EMAIL} --project=pos-backup-project
sudo mkdir -p /etc/tarsierpos
sudo install -m 0640 -o root -g ralph /tmp/gcs-backup-sa.json \
  /etc/tarsierpos/gcs-backup-sa.json
rm /tmp/gcs-backup-sa.json

sudo mkdir -p /etc/systemd/system/tarsierpos-backup-offsite.service.d/
sudo install -m 0644 -o root -g root \
  scripts/systemd/tarsierpos-backup-offsite.service.d/gcs.conf \
  /etc/systemd/system/tarsierpos-backup-offsite.service.d/gcs.conf
sudo systemctl daemon-reload
```

### Per-machine `GCS_DEST`

Each host writes to its own prefix so snapshots don't collide:

| Host           | `GCS_DEST`                                  |
|----------------|---------------------------------------------|
| dev-optiplex   | `gs://tarsierpos-backups-rma/dev-optiplex`  |
| pos-01 (M710q, Phase 2) | `gs://tarsierpos-backups-rma/pos-01` |

The repo's `gcs.conf` ships with `dev-optiplex`. For other hosts edit the
drop-in's `GCS_DEST=` line before installing, or override locally with
`sudo systemctl edit tarsierpos-backup-offsite.service` and reload.

### FLAG-070 ordering note

The 23:30 local-snapshot timer must complete before the 23:45 offsite timer
fires; the offsite script reads the newest `db_[0-9]*.sqlite3` and silently
re-uploads yesterday's snapshot if the 23:30 job hasn't landed yet. Don't
narrow that gap without also adding `After=` / ordering between the two
timers.
