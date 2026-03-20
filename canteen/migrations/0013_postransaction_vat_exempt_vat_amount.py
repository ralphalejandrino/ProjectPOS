# Migration 0013: Add vat_exempt and vat_amount to PosTransaction.
# Part of ISSUE-002 — BIR RA 9994/RA 10754 SC/PWD VAT exemption.
# No backfill needed: existing rows default to False/0.00 (not VAT-exempt).

import decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0012_official_receipt_counter'),
    ]

    operations = [
        migrations.AddField(
            model_name='postransaction',
            name='vat_exempt',
            field=models.BooleanField(
                default=False,
                help_text='True when transaction is VAT-exempt (SC/PWD under RA 9994 / RA 10754)',
            ),
        ),
        migrations.AddField(
            model_name='postransaction',
            name='vat_amount',
            field=models.DecimalField(
                decimal_places=2,
                default=decimal.Decimal('0.00'),
                help_text='VAT amount removed from transaction total (0.00 for non-exempt or VAT-disabled)',
                max_digits=10,
            ),
        ),
    ]
