"""FLAG-047 — tag existing demo/seed PosTransaction rows with is_seed=True.

Seed transactions are the ones created by ``seed_demo`` (the only command that
writes PosTransaction rows). They are identified by signature rather than by
cashier username, because a real deployment's cashier accounts are also named
``cashier1``/``cashier2`` (seed_client) and may ring genuine sales:

  * ``shift IS NULL`` — every real sale is bound to an open shift by
    services.create_pos_transaction (ISSUE-104). seed_demo never assigns a
    shift, so a null shift is the first discriminator.
  * OR sentinel — seed_demo numbers its receipts from 9001 per day
    ("start at 9001 to avoid collision with real counter"), while the live
    OR generator is installation-wide monotonic starting at 1. A NNNN suffix
    >= 9001 is therefore the deliberate seed marker.

Both conditions must hold, so genuine sales (which always carry a shift) are
never mis-tagged. Idempotent: already-tagged rows are skipped.

Usage:
    python manage.py tag_seed_transactions
"""
import re

from django.core.management.base import BaseCommand

from canteen.models import PosTransaction

# OR-YYYYMMDD-NNNN where NNNN is the seed sentinel range (>= 9001).
_OR_RE = re.compile(r'^OR-\d{8}-(\d{4,})$')
_SEED_OR_FLOOR = 9001


class Command(BaseCommand):
    help = 'Tag seed_demo PosTransaction rows with is_seed=True (FLAG-047).'

    def handle(self, *args, **options):
        # Candidate set: untagged, shift-less rows. Real sales always carry a
        # shift, so this already excludes them; the OR sentinel is the final gate.
        candidates = PosTransaction.objects.filter(
            is_seed=False, shift__isnull=True,
        ).values_list('pk', 'transaction_no')

        seed_pks = []
        for pk, transaction_no in candidates:
            match = _OR_RE.match(transaction_no or '')
            if match and int(match.group(1)) >= _SEED_OR_FLOOR:
                seed_pks.append(pk)

        count = (
            PosTransaction.objects.filter(pk__in=seed_pks).update(is_seed=True)
            if seed_pks else 0
        )
        self.stdout.write(self.style.SUCCESS(
            f'Tagged {count} seed transaction(s) with is_seed=True.'
        ))
