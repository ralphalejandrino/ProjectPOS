"""FEATURE-054: backfill PosTransactionItem.unit_cost for pre-snapshot sales.

Sales made before the cost-at-sale snapshot shipped have unit_cost=NULL, so they
contribute 0 to COGS and inflate reported margins. This one-time aid estimates
their cost from each item's CURRENT effective cost (recipe-derived for recipe
items, else the manual purchase_price) — a best-effort proxy, not the true
historical cost-at-sale. It only touches NULL rows, so it is idempotent and safe
to re-run; new sales snapshot their own cost and are never rewritten.

--include-zero extends the same proxy to lines snapshotted at exactly 0: a line
sold while its recipe's ingredients were still uncosted froze a 0 that flatters
COGS identically to a NULL (the #10 margin-honesty hole). After the ingredient
costs are corrected, re-running with this flag re-snapshots those lines at the
now-genuine cost. A zero line whose item STILL has no positive cost is left
untouched (a 0→0 rewrite would falsely mark it re-snapshotted) and reported.
Lines with unit_cost > 0 are never selected, under any flags.

--since YYYY-MM-DD scopes the run to lines whose transaction falls on/after that
PHT date — same `created_at__date` bucketing the weekly report uses. Recommended
together with --include-zero (and a --dry-run first) to keep the rewrite to the
window whose costs were actually corrected.

Variant-aware (C1 parity): each line's cost is derived through
item_effective_unit_cost using the line's RECORDED variant selections
(TransactionItemVariant name-pair snapshots resolved back to options, the same
ISSUE-072 pairing the void/refund restore path uses) — so history for items
whose whole recipe is size-variant-scoped is repaired too, not just base-line
recipes.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from canteen.models import PosTransactionItem, VariantOption
from canteen.services import item_effective_unit_cost

ZERO = Decimal('0')


def _line_option_ids(line, pairset_cache):
    """Resolve the line's snapshot (group_name, option_name) pairs to current
    VariantOption ids — full-pair match (ISSUE-072), cached per pair-set."""
    pairs = frozenset(
        (v.group_name, v.option_name) for v in line.variant_selections.all()
    )
    if not pairs:
        return ()
    if pairs not in pairset_cache:
        f = Q()
        for group_name, option_name in pairs:
            f |= Q(group__name=group_name, name=option_name)
        pairset_cache[pairs] = tuple(
            VariantOption.objects.filter(f).values_list('id', flat=True)
        )
    return pairset_cache[pairs]


class Command(BaseCommand):
    help = "Estimate unit_cost for historical PosTransactionItem rows missing it."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report how many rows would change without writing.',
        )
        parser.add_argument(
            '--include-zero', action='store_true',
            help='Also re-snapshot lines frozen at unit_cost=0 whose item now '
                 'has a positive effective cost (see module docstring).',
        )
        parser.add_argument(
            '--since', metavar='YYYY-MM-DD',
            help='Only touch lines whose transaction is on/after this PHT date.',
        )

    def handle(self, *args, **options):
        dry = options['dry_run']
        include_zero = options['include_zero']

        cond = Q(unit_cost__isnull=True)
        if include_zero:
            cond |= Q(unit_cost=ZERO)
        qs = (PosTransactionItem.objects.filter(cond)
              .select_related('item')
              .prefetch_related('variant_selections'))

        if options['since']:
            try:
                cutoff = date.fromisoformat(options['since'])
            except ValueError:
                raise CommandError(
                    f"--since must be YYYY-MM-DD, got {options['since']!r}"
                )
            qs = qs.filter(pos_transaction__created_at__date__gte=cutoff)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to backfill — all lines already costed.'))
            return

        # Cache per (item, selected options) — the same key the C1 sale-time
        # snapshot varies on — plus per pair-set option resolution.
        cost_cache = {}
        pairset_cache = {}
        updated = 0
        skipped_zero = 0
        with transaction.atomic():
            for line in qs:
                item = line.item
                option_ids = _line_option_ids(line, pairset_cache)
                key = (item.id, option_ids)
                if key not in cost_cache:
                    rc = item_effective_unit_cost(item, option_ids)
                    cost_cache[key] = rc if rc is not None else (item.purchase_price or Decimal('0'))
                cost = cost_cache[key]
                if line.unit_cost is not None and cost <= ZERO:
                    # Zero-frozen line, item still uncosted: leave it be.
                    skipped_zero += 1
                    continue
                line.unit_cost = cost
                if not dry:
                    line.save(update_fields=['unit_cost'])
                updated += 1
            if dry:
                transaction.set_rollback(True)

        verb = 'would update' if dry else 'updated'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {updated} of {total} line(s) across {len(cost_cache)} item/variant combo(s).'
        ))
        if skipped_zero:
            self.stdout.write(self.style.WARNING(
                f'skipped {skipped_zero} zero-cost line(s) whose item still has '
                f'no positive cost — correct the ingredient costs, then re-run.'
            ))
