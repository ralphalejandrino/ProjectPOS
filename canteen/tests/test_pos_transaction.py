"""FEATURE-012 — frozen totals on PosTransaction.

Verifies that services.create_pos_transaction() freezes the correct
figures at commit, that the void path never mutates them, and that
gross_total semantics hold under both VAT-inclusive and VAT-exclusive
pricing.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, PosTransaction, User,
)
from canteen.services import create_pos_transaction

TWOPLACES = Decimal('0.01')


def _D(value):
    return Decimal(str(value)).quantize(TWOPLACES)


class FrozenTotalsBase(APITestCase):
    """Shared fixtures. Each subclass tweaks BusinessProfile VAT config."""

    vat_enabled = True
    vat_inclusive = True
    vat_rate = Decimal('12.00')

    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen',
            currency='PHP',
            vat_enabled=self.vat_enabled,
            vat_inclusive=self.vat_inclusive,
            vat_rate=self.vat_rate,
            sc_discount_rate=Decimal('20.00'),
            pwd_discount_rate=Decimal('20.00'),
            track_inventory=True,
            printer_enabled=False,
        )
        self.admin = User.objects.create_user(
            username='admin', password='x', role='admin'
        )
        self.item_a = Item.objects.create(name='Coffee', price=Decimal('100.00'), stock=1000)
        self.item_b = Item.objects.create(name='Donut', price=Decimal('50.00'), stock=1000)

    def _sell(self, payment_method='cash', **kwargs):
        items_data = kwargs.pop('items_data', [
            {'item_id': self.item_a.id, 'quantity': 2},
            {'item_id': self.item_b.id, 'quantity': 1},
        ])
        return create_pos_transaction(items_data, payment_method, cashier=None, **kwargs)


class NewTransactionMathTests(FrozenTotalsBase):
    """1. New transaction columns match item-level math (cash/gcash/card)."""

    def test_columns_match_item_math_all_payment_methods(self):
        # gross = 100*2 + 50*1 = 250.00 (VAT-inclusive prices)
        for method, ref_kwargs in (
            ('cash', {'cash_received': Decimal('500.00')}),
            ('gcash', {'gcash_reference': 'GC-123'}),
            ('card', {'card_reference': 'CARD-9'}),
        ):
            with self.subTest(payment_method=method):
                txn = self._sell(method, **ref_kwargs)
                txn.refresh_from_db()

                self.assertEqual(txn.gross_total, _D('250.00'))
                self.assertEqual(txn.discount_total, _D('0.00'))
                self.assertEqual(txn.net_total, _D('250.00'))
                self.assertEqual(txn.net_total, _D(txn.total_amount.amount))
                # Non-exempt: legacy vat_amount ("VAT removed") stays 0.00.
                self.assertEqual(txn.vat_amount, _D('0.00'))
                self.assertEqual(txn.vat_exempt_amount, _D('0.00'))
                self.assertEqual(txn.zero_rated_sales, _D('0.00'))
                # VAT-inclusive: output VAT extracted, vatable < net.
                output_vat = (Decimal('250.00') * self.vat_rate
                              / (Decimal('100') + self.vat_rate)).quantize(TWOPLACES)
                self.assertEqual(txn.vatable_sales, _D('250.00') - output_vat)


class ScDiscountTests(FrozenTotalsBase):
    """2. SC discount: discount_total > 0, vat_amount + vat_exempt_amount."""

    def test_sc_discount_freezes_vat_and_exempt(self):
        # Single item priced 112 (VAT-inclusive). VAT-exclusive = 100.00,
        # removed VAT = 12.00, SC discount = 20% of 100 = 20.00.
        item = Item.objects.create(name='Meal', price=Decimal('112.00'), stock=100)
        txn = self._sell(
            'cash',
            items_data=[{'item_id': item.id, 'quantity': 1}],
            discount_amount=Decimal('20.00'),
            discount_type='sc',
            discount_id_number='SC-0001',
            cash_received=Decimal('100.00'),
        )
        txn.refresh_from_db()

        self.assertEqual(txn.gross_total, _D('112.00'))
        self.assertGreater(txn.discount_total, Decimal('0.00'))
        self.assertEqual(txn.discount_total, _D('20.00'))
        # Legacy vat_amount = VAT removed for the exempt sale.
        self.assertEqual(txn.vat_amount, _D('12.00'))
        # net = 112 - 12 (VAT) - 20 (SC) = 80.00, fully VAT-exempt.
        self.assertEqual(txn.net_total, _D('80.00'))
        self.assertEqual(txn.vat_exempt_amount, _D('80.00'))
        self.assertEqual(txn.vatable_sales, _D('0.00'))
        self.assertEqual(txn.zero_rated_sales, _D('0.00'))
        self.assertTrue(txn.vat_exempt)


class VoidImmutabilityTests(FrozenTotalsBase):
    """3. Void must not mutate the frozen columns."""

    FROZEN_FIELDS = (
        'gross_total', 'discount_total', 'vat_amount',
        'vat_exempt_amount', 'vatable_sales', 'zero_rated_sales',
        'net_total',
    )

    def test_void_leaves_frozen_columns_unchanged(self):
        txn = self._sell('cash', cash_received=Decimal('500.00'))
        txn.refresh_from_db()
        before = {f: getattr(txn, f) for f in self.FROZEN_FIELDS}

        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            f'/api/canteen/transactions/{txn.pk}/void/',
            {'reason': 'test void'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        txn.refresh_from_db()
        self.assertEqual(txn.status, 'void')
        self.assertTrue(txn.void)
        after = {f: getattr(txn, f) for f in self.FROZEN_FIELDS}
        self.assertEqual(before, after, 'Void mutated a frozen total column')


class VatInclusiveModeTests(FrozenTotalsBase):
    """4a. vat_inclusive=True — gross embeds VAT, vatable < net."""

    vat_inclusive = True

    def test_gross_is_vat_inclusive(self):
        item = Item.objects.create(name='X', price=Decimal('112.00'), stock=100)
        txn = self._sell('cash', items_data=[{'item_id': item.id, 'quantity': 1}],
                          cash_received=Decimal('200.00'))
        txn.refresh_from_db()
        # gross is the raw pre-discount line sum (VAT embedded in the price).
        self.assertEqual(txn.gross_total, _D('112.00'))
        self.assertEqual(txn.net_total, _D('112.00'))
        # VAT was extracted from the inclusive price -> vatable < net.
        self.assertLess(txn.vatable_sales, txn.net_total)
        self.assertEqual(txn.vatable_sales, _D('100.00'))


class VatExclusiveModeTests(FrozenTotalsBase):
    """4b. vat_inclusive=False — gross has no embedded VAT, vatable == net."""

    vat_inclusive = False

    def test_gross_is_vat_exclusive(self):
        item = Item.objects.create(name='Y', price=Decimal('100.00'), stock=100)
        txn = self._sell('cash', items_data=[{'item_id': item.id, 'quantity': 1}],
                          cash_received=Decimal('200.00'))
        txn.refresh_from_db()
        self.assertEqual(txn.gross_total, _D('100.00'))
        self.assertEqual(txn.net_total, _D('100.00'))
        # No VAT embedded -> vatable equals net charged amount.
        self.assertEqual(txn.vatable_sales, _D('100.00'))
        self.assertEqual(txn.vat_exempt_amount, _D('0.00'))


class ZeroRatedSalesTests(FrozenTotalsBase):
    """5. zero_rated_sales path."""

    def test_zero_rated_sales(self):
        self.skipTest(
            'No zero-rated item flag exists in the schema (Item has no '
            'zero_rated field). zero_rated_sales is structurally always '
            '0.00 until such a flag is introduced; covered as 0.00 by the '
            'other tests.'
        )
