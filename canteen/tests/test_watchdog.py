"""FEATURE-045 — connectivity watchdog script logic.

Runs scripts/network/tarsier-watchdog.sh (and the boot-evidence dump) against
stub binaries, exercising:

  - healthy cycle: counter reset, no actions, quiet log,
  - failures below the 3-check threshold take no action,
  - escalation order from the 3rd consecutive failure: nmcli connection up ->
    restart NetworkManager -> restart tailscaled, one step per cycle, cycling,
  - recovery resets the counter and is logged,
  - dry-run logs intended actions and executes nothing,
  - missing tailscale binary is a skip (dev box), never a failure,
  - boot evidence dump captures nmcli/tailscale/journal sections.

No DB involved — SimpleTestCase.
"""

import os
import stat
import subprocess
import tempfile

from django.test import SimpleTestCase

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WATCHDOG = os.path.join(REPO_ROOT, 'scripts', 'network', 'tarsier-watchdog.sh')
BOOT_EVIDENCE = os.path.join(REPO_ROOT, 'scripts', 'network', 'tarsier-boot-evidence.sh')


class WatchdogHarness(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.ctl = os.path.join(self.dir, 'ctl')
        self.stubs = os.path.join(self.dir, 'stubs')
        self.state = os.path.join(self.dir, 'state')
        os.makedirs(self.ctl)
        os.makedirs(self.stubs)
        self.log = os.path.join(self.dir, 'connectivity.log')
        self.actions = os.path.join(self.dir, 'actions.log')
        self._make_stubs()

    def _stub(self, name, body):
        path = os.path.join(self.stubs, name)
        with open(path, 'w') as f:
            f.write('#!/bin/bash\n' + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def _make_stubs(self):
        ctl, actions = self.ctl, self.actions
        self.ip = self._stub('ip', f'''
if [ -f "{ctl}/no_route" ]; then exit 0; fi
echo "default via 192.168.1.1 dev wlan0 proto dhcp metric 600"
''')
        self.ping = self._stub('ping', f'''
if [ -f "{ctl}/gw_fail" ]; then exit 1; fi
exit 0
''')
        self.tailscale = self._stub('tailscale', f'''
if [ "$1" = "status" ] && [ "$2" = "--json" ]; then
  state="Running"
  [ -f "{ctl}/ts_state" ] && state="$(cat "{ctl}/ts_state")"
  echo "{{\\"BackendState\\": \\"$state\\"}}"
  exit 0
fi
echo "100.100.100.100  pos-01  linux  -"
''')
        self.nmcli = self._stub('nmcli', f'''
echo "nmcli $@" >> "{actions}"
if [ "$1" = "-t" ]; then
  echo "HomeWiFi"
  exit 0
fi
if [ "$1" = "connection" ] && [ -f "{ctl}/nm_fail" ]; then exit 4; fi
exit 0
''')
        self.systemctl = self._stub('systemctl', f'''
echo "systemctl $@" >> "{actions}"
exit 0
''')
        self.journalctl = self._stub('journalctl', '''
echo "journal line for $2"
''')

    def run_watchdog(self, gw_fail=False, ts_state=None, dry_run=False,
                     tailscale_missing=False, nm_fail=False):
        for flag in ('gw_fail', 'no_route', 'ts_state', 'nm_fail'):
            p = os.path.join(self.ctl, flag)
            if os.path.exists(p):
                os.remove(p)
        if gw_fail:
            open(os.path.join(self.ctl, 'gw_fail'), 'w').close()
        if nm_fail:
            open(os.path.join(self.ctl, 'nm_fail'), 'w').close()
        if ts_state is not None:
            with open(os.path.join(self.ctl, 'ts_state'), 'w') as f:
                f.write(ts_state)
        env = dict(os.environ,
                   TARSIER_WD_STATE_DIR=self.state,
                   TARSIER_WD_LOG=self.log,
                   IP=self.ip, PING=self.ping, NMCLI=self.nmcli,
                   SYSTEMCTL=self.systemctl,
                   TAILSCALE=(os.path.join(self.dir, 'missing-tailscale')
                              if tailscale_missing else self.tailscale))
        if dry_run:
            env['TARSIER_WD_DRY_RUN'] = '1'
        return subprocess.run(['bash', WATCHDOG], env=env,
                              capture_output=True, text=True, timeout=30)

    def read(self, path):
        if not os.path.exists(path):
            return ''
        with open(path) as f:
            return f.read()

    @property
    def failcount(self):
        return self.read(os.path.join(self.state, 'failcount')).strip()


class WatchdogCheckTests(WatchdogHarness):
    def test_healthy_cycle_writes_heartbeat_and_resets_counter(self):
        r = self.run_watchdog()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.failcount, '0')
        log = self.read(self.log)
        self.assertNotIn('CHECK FAILED', log)
        self.assertEqual(self.read(self.actions), '')
        # One heartbeat line per run: "<ts> ... OK gw=<ip> ts=<state> fails=0"
        self.assertIn('OK gw=192.168.1.1 ts=Running fails=0', log)
        self.assertRegex(
            log, r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4} \[watchdog\] '
                 r'OK gw=192\.168\.1\.1 ts=Running fails=0')

    def test_heartbeat_written_every_healthy_run(self):
        self.run_watchdog()
        self.run_watchdog()
        self.run_watchdog()
        log = self.read(self.log)
        self.assertEqual(log.count('OK gw=192.168.1.1 ts=Running fails=0'), 3)

    def test_failures_below_threshold_take_no_action(self):
        self.run_watchdog(gw_fail=True)
        self.run_watchdog(gw_fail=True)
        self.assertEqual(self.failcount, '2')
        log = self.read(self.log)
        # Failed checks name the failed probe and the new consecutive count.
        self.assertIn('CHECK FAILED (1 consecutive) probes=[gateway]', log)
        self.assertIn('CHECK FAILED (2 consecutive) probes=[gateway]', log)
        self.assertEqual(self.read(self.actions), '')

    def test_recovery_resets_counter_and_logs(self):
        self.run_watchdog(gw_fail=True)
        self.run_watchdog()
        self.assertEqual(self.failcount, '0')
        self.assertIn('RECOVERED after 1 fails', self.read(self.log))

    def test_tailscale_failure_alone_counts_as_failure(self):
        self.run_watchdog(ts_state='Stopped')
        self.assertEqual(self.failcount, '1')
        log = self.read(self.log)
        self.assertIn('BackendState=Stopped', log)
        self.assertIn('probes=[tailscale]', log)

    def test_missing_tailscale_is_skipped_not_failed(self):
        r = self.run_watchdog(tailscale_missing=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.failcount, '0')
        self.assertEqual(self.read(self.actions), '')


class WatchdogEscalationTests(WatchdogHarness):
    def test_escalation_order_one_step_per_cycle_then_cycles(self):
        for _ in range(6):
            self.run_watchdog(gw_fail=True)
        actions = [l for l in self.read(self.actions).splitlines()
                   if 'connection up' in l or 'restart' in l]
        self.assertEqual(actions, [
            'nmcli connection up HomeWiFi',
            'systemctl restart NetworkManager',
            'systemctl restart tailscaled',
            'nmcli connection up HomeWiFi',   # cycle restarts, never reboots
        ])
        log = self.read(self.log)
        self.assertIn('STATE at escalation: failcount=3', log)
        self.assertNotIn('reboot', log.lower())

    def test_escalation_logs_action_and_exit_status(self):
        for _ in range(3):
            self.run_watchdog(gw_fail=True)
        log = self.read(self.log)
        self.assertIn("ACTION: nmcli connection up 'HomeWiFi' (escalation 1/3)", log)
        self.assertIn("ACTION OK (rc=0): nmcli connection up 'HomeWiFi'", log)

    def test_failed_escalation_action_logs_exit_status(self):
        for _ in range(3):
            self.run_watchdog(gw_fail=True, nm_fail=True)
        self.assertIn('ACTION FAILED (rc=4):', self.read(self.log))

    def test_dry_run_prints_decision_path_and_executes_nothing(self):
        outs = [self.run_watchdog(gw_fail=True, dry_run=True).stdout
                for _ in range(3)]
        # stdout must carry the full decision path, every run.
        for out in outs:
            self.assertTrue(out.strip(), 'dry-run stdout was empty')
            self.assertIn('DRY-RUN: check gateway:', out)
            self.assertIn('DRY-RUN: check tailscale:', out)
            self.assertIn('failcount', out)
        self.assertIn('failcount 0 -> 1', outs[0])
        self.assertIn('below threshold -> no escalation', outs[0])
        self.assertIn('failcount 2 -> 3', outs[2])
        self.assertIn('escalation rung 1/3 selected', outs[2])
        self.assertIn("DRY-RUN: would run: nmcli connection up 'HomeWiFi'", outs[2])
        # The decision path is mirrored to the log, and nothing executed.
        self.assertIn('DRY-RUN: would run', self.read(self.log))
        actions = self.read(self.actions)
        self.assertNotIn('connection up', actions)
        self.assertNotIn('restart', actions)

    def test_dry_run_healthy_prints_heartbeat_path(self):
        out = self.run_watchdog(dry_run=True).stdout
        self.assertIn('DRY-RUN: check gateway:', out)
        self.assertIn('PASS', out)
        self.assertIn('heartbeat written', out)

    def test_recovery_after_escalation_resets_escalation_ladder(self):
        for _ in range(3):
            self.run_watchdog(gw_fail=True)
        self.run_watchdog()           # recovered
        for _ in range(3):
            self.run_watchdog(gw_fail=True)
        actions = [l for l in self.read(self.actions).splitlines()
                   if 'connection up' in l or 'restart' in l]
        # Both outages start the ladder from step 1 (nmcli), not where it left off.
        self.assertEqual(actions, [
            'nmcli connection up HomeWiFi',
            'nmcli connection up HomeWiFi',
        ])


class BootEvidenceTests(WatchdogHarness):
    def test_boot_dump_captures_all_sections(self):
        env = dict(os.environ,
                   TARSIER_WD_LOG=self.log,
                   NMCLI=self.nmcli, TAILSCALE=self.tailscale,
                   JOURNALCTL=self.journalctl)
        r = subprocess.run(['bash', BOOT_EVIDENCE], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.read(self.log)
        self.assertIn('[boot-evidence] boot dump', log)
        self.assertIn('nmcli connection show', log)
        self.assertIn('autoconnect priorities', log)
        self.assertIn('tailscale status', log)
        self.assertIn('NetworkManager journal', log)
        self.assertIn('tailscaled journal', log)
        self.assertIn('[boot-evidence] dump complete', log)
