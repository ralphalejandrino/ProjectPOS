"""FEATURE-054: backfill PosTransactionItem.unit_cost for pre-snapshot sales.

Sales made before the cost-at-sale snapshot shipped have unit_cost=NULL, so they
contribute 0 to COGS and inflate reported margins. This one-time aid estimates
their cost from each item's CURRENT effective cost (recipe-derived for recipe
items, else the manual purchase_price) — a best-effort proxy, not the true
historical cost-at-sale. It only touches NULL rows, so it is idempotent and safe
to re-run; new sales snapshot their own cost and are never rewritten.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from canteen.models import PosTransactionItem
from canteen.services import item_recipe_cost


class Command(BaseCommand):
    help = "Estimate unit_cost for historical PosTransactionItem rows missing it."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report how many rows would change without writing.',
        )

    def handle(self, *args, **options):
        dry = options['dry_run']
        qs = PosTransactionItem.objects.filter(unit_cost__isnull=True).select_related('item')
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to backfill — all lines already costed.'))
            return

        # Cache per-item effective cost to avoid recomputing the recipe per line.
        cost_cache = {}
        updated = 0
        with transaction.atomic():
            for line in qs.iterator():
                item = line.item
                if item.id not in cost_cache:
                    rc = item_recipe_cost(item)
                    cost_cache[item.id] = rc if rc is not None else (item.purchase_price or Decimal('0'))
                line.unit_cost = cost_cache[item.id]
                if not dry:
                    line.save(update_fields=['unit_cost'])
                updated += 1
            if dry:
                transaction.set_rollback(True)

        verb = 'would update' if dry else 'updated'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {updated} of {total} line(s) across {len(cost_cache)} item(s).'
        ))
