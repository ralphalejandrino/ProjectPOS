"""FEATURE-012: persist frozen totals on PosTransaction.

Adds six frozen-total columns to ``PosTransaction``. The existing
``vat_amount`` column is deliberately NOT added here: it pre-existed
(migration 0009 lineage) as a DecimalField, is always written at commit
inside ``services.create_pos_transaction()``, and is never mutated after
commit (including the void path). Its semantics are confirmed unchanged:
"VAT removed from total for SC/PWD exempt; 0.00 for non-exempt or
VAT-disabled". It is part of the frozen set but needs no schema change.

``gross_total`` and ``net_total`` are nullable so this AddField does not
require a one-off default on the historical table; migration 0024
backfills them.
"""

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0022_alter_paymentgatewayconfig_gateway_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='postransaction',
            name='gross_total',
            field=models.DecimalField(
                max_digits=12,
                decimal_places=2,
                null=True,
                blank=True,
                help_text='Frozen pre-discount total (VAT-inclusive when BusinessProfile.vat_inclusive)',
            ),
        ),
        migrations.AddField(
            model_name='postransaction',
            name='discount_total',
            field=models.DecimalField(
                max_digits=12,
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Frozen discount applied to this transaction',
            ),
        ),
        migrations.AddField(
            model_name='postransaction',
            name='vat_exempt_amount',
            field=models.DecimalField(
                max_digits=12,
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Frozen VAT-exempt sales (net of removed VAT for SC/PWD)',
            ),
        ),
        migrations.AddField(
            model_name='postransaction',
            name='vatable_sales',
            field=models.DecimalField(
                max_digits=12,
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Frozen VAT-exclusive sales subject to output VAT',
            ),
        ),
        migrations.AddField(
            model_name='postransaction',
            name='zero_rated_sales',
            field=models.DecimalField(
                max_digits=12,
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Frozen zero-rated sales (no zero-rated item flag exists yet — always 0.00)',
            ),
        ),
        migrations.AddField(
            model_name='postransaction',
            name='net_total',
            field=models.DecimalField(
                max_digits=12,
                decimal_places=2,
                null=True,
                blank=True,
                help_text='Frozen final charged amount (equals total_amount at commit)',
            ),
        ),
    ]
