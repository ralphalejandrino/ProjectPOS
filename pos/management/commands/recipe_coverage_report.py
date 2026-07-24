"""FLAG-045: report items that have no RecipeIngredient linked.

34/70 items currently have no recipe, so selling them depletes no ingredient
stock — a silent inventory gap. This read-only command lists those items and
flags the high-priority ones: items with no recipe that were still SOLD in the
last 30 days (cross-referenced against PosTransactionItem, excluding voids).

"Linked" here means an item-level RecipeIngredient (RecipeIngredient.item).
Variant-only recipes are not counted as item coverage — same signal the Item
admin "Recipe linked" column shows.

Read-only: no migration, no writes.

Usage:
    python manage.py recipe_coverage_report
    python manage.py recipe_coverage_report --days 60
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Sum
from django.utils import timezone

from pos.models import Item, PosTransactionItem


class Command(BaseCommand):
    help = "List items with no RecipeIngredient linked; flag those sold recently."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=30,
            help="Recency window for the high-priority sold flag (default 30).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        cutoff = timezone.now() - timedelta(days=days)

        # Items with no item-level RecipeIngredient.
        items = (
            Item.objects
            .annotate(_recipe_count=Count("recipe_ingredients"))
            .filter(_recipe_count=0)
            .order_by("name")
        )

        # Recent, non-voided sales aggregated per item.
        sales = (
            PosTransactionItem.objects
            .filter(
                pos_transaction__created_at__gte=cutoff,
                pos_transaction__voided_at__isnull=True,
            )
            .values("item_id")
            .annotate(last_sold=Max("pos_transaction__created_at"),
                      sold_count=Sum("quantity"))
        )
        sales_by_item = {row["item_id"]: row for row in sales}

        total = items.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                "All items have a RecipeIngredient linked — no coverage gaps."
            ))
            return

        high_priority = []
        low_priority = []
        for item in items:
            row = sales_by_item.get(item.id)
            if row:
                high_priority.append((item, row))
            else:
                low_priority.append(item)

        self.stdout.write(
            f"Recipe coverage gap: {total} item(s) with no RecipeIngredient linked.\n"
        )

        self.stdout.write(self.style.WARNING(
            f"HIGH PRIORITY — sold in the last {days} days ({len(high_priority)}):"
        ))
        if high_priority:
            header = f"  {'ITEM ID':<38} {'ITEM NAME':<30} {'LAST SOLD':<20} {'SOLD (30d)':>10}"
            self.stdout.write(header)
            for item, row in high_priority:
                last_sold = timezone.localtime(row["last_sold"]).strftime("%Y-%m-%d %H:%M")
                self.stdout.write(
                    f"  {str(item.id):<38} {(item.name or '—'):<30.30} "
                    f"{last_sold:<20} {row['sold_count']:>10}"
                )
        else:
            self.stdout.write("  (none)")

        self.stdout.write("")
        self.stdout.write(
            f"NOT SOLD in the last {days} days ({len(low_priority)}):"
        )
        if low_priority:
            for item in low_priority:
                self.stdout.write(f"  {str(item.id):<38} {item.name or '—'}")
        else:
            self.stdout.write("  (none)")
