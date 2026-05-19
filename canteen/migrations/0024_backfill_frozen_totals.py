"""FEATURE-012: backfill frozen totals for historical PosTransaction rows.

RunPython only. Recomputes the frozen columns from the existing
``PosTransactionItem`` rows using the SAME arithmetic as
``services.create_pos_transaction()`` at the time this migration was
written, then freezes the result.

IMPORTANT — historical fidelity caveat:
    Pre-migration receipts were NOT generated with these stored columns.
    This backfill reconstructs the figures from line-item subtotals and
    the CURRENT BusinessProfile VAT configuration (vat_enabled, vat_rate,
    vat_inclusive). It therefore reflects code-and-config *at migration
    time*, NOT necessarily the numbers printed on the original receipt
    (e.g. if the VAT rate or inclusive flag changed since the sale).
    From migration 0023 forward, services.create_pos_transaction() is the
    single source of truth and these columns are authoritative.

``vat_amount`` pre-existed (migration 0009 lineage); its semantics are
confirmed unchanged ("VAT removed for SC/PWD exempt; 0.00 otherwise").
It is recomputed here only to repair any stale/null historical values.

Reverse is an intentional no-op: the columns stay populated (rolling
back the schema in 0023 is what would drop them).
"""

from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations

_Q = Decimal('0.01')


def _freeze_historical_totals(apps, schema_editor):
    PosTransaction = apps.get_model('canteen', 'PosTransaction')
    BusinessProfile = apps.get_model('canteen', 'BusinessProfile')

    bp = BusinessProfile.objects.first()
    vat_enabled = bool(bp and bp.vat_enabled)
    vat_inclusive = bool(bp and bp.vat_inclusive)
    # Null-aware VAT rate read from BusinessProfile — never a literal 12/0.12.
    vat_rate = (
        Decimal(str(bp.vat_rate))
        if (bp is not None and bp.vat_rate is not None)
        else Decimal('0')
    )

    qs = PosTransaction.objects.all().prefetch_related('items')
    for txn in qs.iterator(chunk_size=500):
        gross = Decimal('0.00')
        for line in txn.items.all():
            if line.subtotal is not None:
                gross += Decimal(str(line.subtotal))
        gross = gross.quantize(_Q, rounding=ROUND_HALF_UP)

        discount = Decimal(str(txn.discount_amount or 0)).quantize(
            _Q, rounding=ROUND_HALF_UP
        )

        # total_amount may be a Money instance on the historical model.
        net_raw = getattr(txn.total_amount, 'amount', txn.total_amount)
        net = Decimal(str(net_raw or 0)).quantize(_Q, rounding=ROUND_HALF_UP)

        is_vat_exempt = bool(getattr(txn, 'vat_exempt', False))

        # Recompute the "VAT removed" amount the same way services.py does
        # (SC/PWD on the pre-discount gross). 0.00 when not exempt/enabled.
        if is_vat_exempt and vat_enabled and vat_rate > 0:
            vat_exclusive = (
                gross / (Decimal('1') + vat_rate / Decimal('100'))
            ).quantize(_Q, rounding=ROUND_HALF_UP)
            removed_vat = (gross - vat_exclusive).quantize(
                _Q, rounding=ROUND_HALF_UP
            )
        else:
            removed_vat = Decimal('0.00')

        zero_rated = Decimal('0.00')
        if is_vat_exempt:
            vat_exempt_amount = net
            vatable = Decimal('0.00')
        elif vat_enabled and vat_rate > 0:
            vat_exempt_amount = Decimal('0.00')
            if vat_inclusive:
                output_vat = (
                    net * vat_rate / (Decimal('100') + vat_rate)
                ).quantize(_Q, rounding=ROUND_HALF_UP)
                vatable = (net - output_vat).quantize(
                    _Q, rounding=ROUND_HALF_UP
                )
            else:
                vatable = net
        else:
            vat_exempt_amount = Decimal('0.00')
            vatable = Decimal('0.00')

        PosTransaction.objects.filter(pk=txn.pk).update(
            gross_total=gross,
            discount_total=discount,
            vat_amount=removed_vat,
            vat_exempt_amount=vat_exempt_amount,
            vatable_sales=vatable,
            zero_rated_sales=zero_rated,
            net_total=net,
        )


def _noop_reverse(apps, schema_editor):
    """Intentional no-op — frozen columns remain populated on rollback."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0023_postransaction_frozen_totals'),
    ]

    operations = [
        migrations.RunPython(_freeze_historical_totals, _noop_reverse),
    ]
