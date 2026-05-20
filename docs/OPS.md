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
