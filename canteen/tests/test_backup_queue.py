"""FEATURE-017(c) — offline queue + hourly flush for failed GCS uploads.

Tests shell scripts/backup/{offsite-backup,retry-queue}.sh through a
fake `gsutil` on PATH. No real GCP calls.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path("/home/ralph/TarsierPOS")
OFFSITE = REPO / "scripts/backup/offsite-backup.sh"
RETRY = REPO / "scripts/backup/retry-queue.sh"


class _BackupShellHarness(unittest.TestCase):
    """Sandbox: temp BACKUP_DIR, QUEUE_DIR, AUDIT_LOG and a fake gsutil."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bk017c-"))
        self.backup_dir = self.tmp / "backups"
        self.queue_dir = self.tmp / "queue"
        self.audit_log = self.tmp / "audit.log"
        self.bin_dir = self.tmp / "bin"
        for d in (self.backup_dir, self.queue_dir, self.bin_dir):
            d.mkdir()
        # A snapshot the script can pick up.
        self.snap = self.backup_dir / "db_20260101_000000.sqlite3"
        self.snap.write_bytes(b"snapshot")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_fake_gsutil(self, exit_code: int):
        fake = self.bin_dir / "gsutil"
        fake.write_text(textwrap.dedent(f"""\
            #!/bin/bash
            echo "fake-gsutil $*" >&2
            exit {exit_code}
        """))
        fake.chmod(0o755)

    def _env(self, gcs_dest="gs://fake-bucket/x", **extra):
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.bin_dir}:{env['PATH']}",
            "BACKUP_DIR": str(self.backup_dir),
            "USB_MOUNT": str(self.tmp / "no-mount"),
            "AUDIT_LOG": str(self.audit_log),
            "QUEUE_DIR": str(self.queue_dir),
            "BACKOFF_INITIAL": "0",
        })
        if gcs_dest is not None:
            env["GCS_DEST"] = gcs_dest
        else:
            env.pop("GCS_DEST", None)
        env.update(extra)
        return env

    def _run(self, script, env):
        return subprocess.run(
            ["/bin/bash", str(script)],
            env=env, capture_output=True, text=True, timeout=30,
        )


class OffsiteBackupQueueTests(_BackupShellHarness):
    def test_queue_file_written_on_all_attempts_failed(self):
        self._write_fake_gsutil(exit_code=1)
        r = self._run(OFFSITE, self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.audit_log.read_text()
        self.assertIn("gcs FAIL attempt=1/3", log)
        self.assertIn("gcs FAIL attempt=3/3", log)
        self.assertIn("gcs ABORT", log)
        self.assertIn("gcs QUEUE wrote", log)
        pending = list(self.queue_dir.glob("*.pending"))
        self.assertEqual(len(pending), 1, pending)
        self.assertEqual(pending[0].read_text().strip(), str(self.snap))

    def test_no_queue_file_on_success(self):
        self._write_fake_gsutil(exit_code=0)
        r = self._run(OFFSITE, self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.audit_log.read_text()
        self.assertIn("gcs OK attempt=1", log)
        self.assertEqual(list(self.queue_dir.glob("*.pending")), [])


class RetryQueueTests(_BackupShellHarness):
    def _enqueue(self, source: Path, name="20260101T000000.pending"):
        f = self.queue_dir / name
        f.write_text(f"{source}\n")
        return f

    def test_retry_deletes_pending_on_success(self):
        self._write_fake_gsutil(exit_code=0)
        pending = self._enqueue(self.snap)
        r = self._run(RETRY, self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.audit_log.read_text()
        self.assertIn("gcs RETRY OK attempt=1", log)
        self.assertFalse(pending.exists())

    def test_retry_all_fail_leaves_pending_intact(self):
        self._write_fake_gsutil(exit_code=1)
        pending = self._enqueue(self.snap)
        r = self._run(RETRY, self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.audit_log.read_text()
        self.assertIn("gcs RETRY FAIL attempt=3/3", log)
        self.assertIn("gcs RETRY ABORT", log)
        self.assertTrue(pending.exists())
        self.assertEqual(pending.read_text().strip(), str(self.snap))

    def test_empty_queue_logs_and_exits_zero(self):
        self._write_fake_gsutil(exit_code=1)  # shouldn't be called
        r = self._run(RETRY, self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("queue empty", self.audit_log.read_text())

    def test_skip_when_gcs_dest_unset(self):
        self._write_fake_gsutil(exit_code=1)  # shouldn't be called
        self._enqueue(self.snap)  # queued but unconfigured
        r = self._run(RETRY, self._env(gcs_dest=None))
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.audit_log.read_text()
        self.assertIn("gcs RETRY SKIP GCS_DEST not configured", log)
        # Queue untouched.
        self.assertEqual(len(list(self.queue_dir.glob("*.pending"))), 1)
