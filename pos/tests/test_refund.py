"""FEATURE-015 — refund accounting.

A refund is distinct from a void: the original sale is left untouched and a new
negative transaction (transaction_type='refund', refund_of=original) is posted
to the refunder's current open shift. Refunds restore ingredient stock and
surface as a separate deduction line in the X/Z reports. Cashiers cannot refund.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, Item, Shift, User,
    IngredientUnit, Ingredient, RecipeIngredient,
    PosTransaction, IngredientLog,
)
from pos.services import create_pos_transaction, close_shift_and_finalize_z


class RefundTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Pos',
            currency='PHP',
            vat_enabled=False,
            track_inventory=True,
            printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.shift = Shift.objects.create(
            cashier=self.manager, opening_cash=Decimal('1000.00'), is_open=True
        )
        self.item = Item.objects.create(
            name='Latte', price=Decimal('100.00'), stock=1000
        )
        unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )
        self.START = Decimal('100.0000')
        self.beans = Ingredient.objects.create(
            name='Beans', unit=unit, cost_per_unit=Decimal('1.0000'),
            current_stock=self.START,
        )
        RecipeIngredient.objects.create(
            item=self.item, ingredient=self.beans,
            quantity_used=Decimal('5.0000'),
        )

    def _ring_sale(self, cashier=None):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}],
            'cash',
            cashier=cashier or self.manager,
            cash_received=Decimal('200.00'),
        )

    def test_refund_endpoint_creates_negative_transaction(self):
        sale = self._ring_sale()
        self.client.force_authenticate(self.manager)
        resp = self.client.post(f'/api/pos/transactions/{sale.id}/refund/')
        self.assertEqual(
            resp.status_code, status.HTTP_201_CREATED,
            f"Expected 201, got {resp.status_code}: {resp.data}",
        )
        refund = PosTransaction.objects.get(transaction_type='refund')
        self.assertEqual(refund.refund_of_id, sale.id)
        self.assertEqual(refund.gross_total, -sale.gross_total)
        self.assertEqual(refund.net_total, -sale.net_total)
        self.assertFalse(refund.is_seed)
        self.assertEqual(refund.payment_method, sale.payment_method)
        # Original is untouched.
        sale.refresh_from_db()
        self.assertEqual(sale.transaction_type, 'sale')
        self.assertFalse(sale.void)

    def test_refund_restores_ingredient_stock(self):
        sale = self._ring_sale()
        self.beans.refresh_from_db()
        self.assertEqual(self.beans.current_stock, self.START - Decimal('5.0000'))

        self.client.force_authenticate(self.manager)
        resp = self.client.post(f'/api/pos/transactions/{sale.id}/refund/')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        self.beans.refresh_from_db()
        self.assertEqual(
            self.beans.current_stock, self.START,
            "refund must restore the depleted ingredient stock",
        )
        # Ledger row tagged with the refund action.
        self.assertTrue(
            IngredientLog.objects.filter(
                ingredient=self.beans, action='refund'
            ).exists()
        )

    def test_cashier_cannot_refund(self):
        sale = self._ring_sale()
        self.client.force_authenticate(self.cashier)
        resp = self.client.post(f'/api/pos/transactions/{sale.id}/refund/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            PosTransaction.objects.filter(transaction_type='refund').exists()
        )

    def test_refund_totals_in_xreport(self):
        sale = self._ring_sale()
        self.client.force_authenticate(self.manager)
        self.client.post(f'/api/pos/transactions/{sale.id}/refund/')

        resp = self.client.get('/api/pos/transactions/xreport/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['refund_count'], 1)
        self.assertEqual(resp.data['refund_total'], 100.0)
        # The refund must not inflate the sales transaction count.
        self.assertEqual(resp.data['transaction_count'], 1)

    def test_refund_totals_in_zreport(self):
        sale = self._ring_sale()
        self.client.force_authenticate(self.manager)
        self.client.post(f'/api/pos/transactions/{sale.id}/refund/')

        z = close_shift_and_finalize_z(
            self.shift.id, cash_counted=None, cashier_user=self.manager
        )
        self.assertEqual(z.refund_count, 1)
        self.assertEqual(z.refund_total, Decimal('100.00'))
        # Sales aggregates stay pure (refund excluded from gross/net sales).
        self.assertEqual(z.gross_sales, Decimal('100.00'))
        self.assertEqual(z.transaction_count, 1)
        # Cash refunded reduces expected drawer cash.
        # opening 1000 + 100 cash sale - 100 cash refund = 1000.
        self.assertEqual(z.cash_expected, Decimal('1000.00'))

    def test_cannot_refund_twice(self):
        sale = self._ring_sale()
        self.client.force_authenticate(self.manager)
        first = self.client.post(f'/api/pos/transactions/{sale.id}/refund/')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post(f'/api/pos/transactions/{sale.id}/refund/')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            PosTransaction.objects.filter(transaction_type='refund').count(), 1
        )
