"""FEATURE-023: snapshot the database before running migrations.

Migrations otherwise run live on prod with no safety net. ``safe_migrate``
takes a consistent SQLite snapshot, verifies it is non-empty, then runs
``migrate``. If migrate fails it prints copy-paste rollback steps and exits
non-zero, leaving the live DB untouched.

Portable: the snapshot directory is derived from ``settings.BASE_DIR`` — no
hardcoded paths. On dev that resolves to <repo>/backups/, on pos-01 to
whatever BASE_DIR points at on that box.

Usage:
    python manage.py safe_migrate
    python manage.py safe_migrate pos 0033 --noinput
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Snapshot the SQLite DB to backups/pre-migrate-<ts>.sqlite3, then run migrate."

    def add_arguments(self, parser):
        parser.add_argument(
            "migrate_args", nargs="*",
            help="Positional args forwarded to migrate (e.g. app_label migration_name).",
        )
        parser.add_argument(
            "--noinput", "--no-input", action="store_true", dest="noinput",
            help="Forwarded to migrate (non-interactive).",
        )
        parser.add_argument(
            "--fake", action="store_true", dest="fake",
            help="Forwarded to migrate (mark as run without applying).",
        )

    def handle(self, *args, **opts):
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "")
        if "sqlite3" not in engine:
            raise CommandError(
                f"safe_migrate only supports SQLite snapshots (DATABASES engine is {engine})."
            )
        db_path = Path(db["NAME"])
        if not db_path.exists():
            raise CommandError(f"Database not found at {db_path}; nothing to snapshot.")

        backup_dir = Path(settings.BASE_DIR) / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot = backup_dir / f"pre-migrate-{ts}.sqlite3"

        # Consistent online-backup snapshot (captures WAL contents too).
        self.stdout.write(f"Snapshotting {db_path}\n        -> {snapshot}")
        try:
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(snapshot))
            with dst:
                src.backup(dst)
        finally:
            try:
                src.close()
                dst.close()
            except NameError:
                pass

        size = snapshot.stat().st_size if snapshot.exists() else 0
        if size == 0:
            if snapshot.exists():
                snapshot.unlink()
            raise CommandError("Snapshot is 0 bytes — aborting BEFORE migrate.")
        self.stdout.write(self.style.SUCCESS(f"Snapshot OK ({size:,} bytes)."))

        migrate_args = list(opts.get("migrate_args") or [])
        migrate_kwargs = {}
        if opts.get("noinput"):
            migrate_kwargs["interactive"] = False
        if opts.get("fake"):
            migrate_kwargs["fake"] = True

        try:
            self.stdout.write("Running migrate...")
            call_command("migrate", *migrate_args, **migrate_kwargs)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"\nmigrate FAILED: {exc}"))
            self._print_rollback(snapshot, db_path)
            raise CommandError("Migration failed — DB left as-is; follow rollback steps above.")

        self.stdout.write(self.style.SUCCESS(f"\nMigration complete. Snapshot retained:\n  {snapshot}"))

    def _print_rollback(self, snapshot, db_path):
        service = os.environ.get("TARSIERPOS_SERVICE", "tarsierpos-backend")
        self.stderr.write(self.style.WARNING(
            "\n──────────────── ROLLBACK ────────────────\n"
            "The migration failed. The live DB was NOT rolled back automatically.\n"
            "If the DB is in a bad state, restore the pre-migrate snapshot:\n\n"
            f"  sudo systemctl stop {service}\n"
            f"  cp \"{snapshot}\" \"{db_path}\"\n"
            f"  sudo systemctl start {service}\n\n"
            "Then investigate the migration before retrying.\n"
            "───────────────────────────────────────────"
        ))
