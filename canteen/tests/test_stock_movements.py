"""FEATURE-008 — stock movement section on X + Z reports.

Covers the backend feed read live from the IngredientLog ledger:
  (1) the X-report endpoint returns a stock_movements key on an open shift,
  (2) sold/voided quantities match the ledger rows after a transaction,
  (3) ingredients with FLAG-046 track_depletion=False produce no rows.
Plus the Z-report serializer exposing the same data for a closed shift.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User,
    IngredientUnit, Ingredient, RecipeIngredient,
)
from canteen.serializers import ZReportSerializer
from canteen.services import (
    create_pos_transaction, _restore_ingredients,
    close_shift_and_finalize_z,
)


class StockMovementBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )
        # Tracked ingredient + item (recipe uses 2.0 per unit sold).
        self.ing = Ingredient.objects.create(
            name='Beans', unit=self.unit, cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal('100.0000'),
        )
        self.item = Item.objects.create(
            name='Brewed Coffee', price=Decimal('50.00'), stock=1000
        )
        RecipeIngredient.objects.create(
            item=self.item, ingredient=self.ing, quantity_used=Decimal('2.0000')
        )
        # Untracked ingredient + item (FLAG-046 track_depletion=False).
        self.ing_untracked = Ingredient.objects.create(
            name='Tap Water', unit=self.unit, cost_per_unit=Decimal('0.0000'),
            current_stock=Decimal('100.0000'), track_depletion=False,
        )
        self.item_untracked = Item.objects.create(
            name='Iced Water', price=Decimal('10.00'), stock=1000
        )
        RecipeIngredient.objects.create(
            item=self.item_untracked, ingredient=self.ing_untracked,
            quantity_used=Decimal('5.0000'),
        )

    def _sell(self, item, qty):
        return create_pos_transaction(
            [{'item_id': item.id, 'quantity': qty}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )


class XReportStockMovementTests(StockMovementBase):
    def test_xreport_returns_stock_movements_key(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.get('/api/canteen/transactions/xreport/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('stock_movements', resp.data)
        # Nothing sold yet → empty list, not missing.
        self.assertEqual(resp.data['stock_movements'], [])

    def test_sold_voided_counts_match_ledger(self):
        self._sell(self.item, qty=3)   # 3 * 2.0 = 6.0 sold
        self.client.force_authenticate(self.cashier)
        resp = self.client.get('/api/canteen/transactions/xreport/')
        self.assertEqual(resp.status_code, 200)
        movements = resp.data['stock_movements']
        self.assertEqual(len(movements), 1)
        row = movements[0]
        self.assertEqual(row['ingredient_id'], self.ing.id)
        self.assertEqual(row['ingredient_name'], 'Beans')
        self.assertEqual(row['sold'], 6.0)
        self.assertEqual(row['voided'], 0.0)

    def test_void_increments_voided_column(self):
        txn = self._sell(self.item, qty=2)   # 4.0 sold
        for item_entry in txn.items.all():
            _restore_ingredients(
                item_entry.item, item_entry, item_entry.quantity,
                transaction=txn, performed_by=self.cashier,
            )
        self.client.force_authenticate(self.cashier)
        resp = self.client.get('/api/canteen/transactions/xreport/')
        movements = resp.data['stock_movements']
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0]['sold'], 4.0)
        self.assertEqual(movements[0]['voided'], 4.0)

    def test_track_depletion_false_produces_no_rows(self):
        self._sell(self.item_untracked, qty=5)   # untracked → no ledger rows
        self.client.force_authenticate(self.cashier)
        resp = self.client.get('/api/canteen/transactions/xreport/')
        self.assertEqual(resp.data['stock_movements'], [])


class ZReportStockMovementTests(StockMovementBase):
    def test_serializer_exposes_stock_movements_for_closed_shift(self):
        self._sell(self.item, qty=4)   # 8.0 sold
        z = close_shift_and_finalize_z(
            self.shift.id, Decimal('0.00'), self.cashier
        )
        data = ZReportSerializer(z).data
        self.assertIn('stock_movements', data)
        self.assertEqual(len(data['stock_movements']), 1)
        self.assertEqual(data['stock_movements'][0]['ingredient_name'], 'Beans')
        self.assertEqual(data['stock_movements'][0]['sold'], 8.0)
