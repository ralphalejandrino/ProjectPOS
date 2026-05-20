# Operations

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
