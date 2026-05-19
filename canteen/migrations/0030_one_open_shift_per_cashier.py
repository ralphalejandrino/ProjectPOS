"""ISSUE-104: enforce at most one open shift per cashier.

Adds a partial unique constraint (cashier) WHERE is_open=True so a cashier
cannot have two open shifts at once. Closed shifts are unconstrained, so the
close-then-reopen flow is unaffected.

Pre-migrate audit (run on the deploy box 2026-05-19):
    Shift.objects.filter(is_open=True).values('cashier')
        .annotate(c=Count('id')).filter(c__gt=1)  ->  []
Zero cashiers had duplicate open shifts, so NO dedup data migration is
required. Had any existed, the documented remedy would have been: keep the
newest open shift per cashier and close the rest with
closed_at=opened_at / closing_cash=opening_cash (net-zero impact).

PosTransaction.shift FK: already added by FEATURE-011-C (migration 0027,
nullable SET_NULL, indexed). No FK add or historical backfill needed here.

Reversible: RemoveConstraint -> no-op (the constraint simply disappears).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0029_zreport_is_official'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='shift',
            constraint=models.UniqueConstraint(
                fields=['cashier'],
                condition=models.Q(is_open=True),
                name='one_open_shift_per_cashier',
            ),
        ),
    ]
