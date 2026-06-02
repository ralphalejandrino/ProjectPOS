"""FLAG-047 — seed/demo transaction quarantine.

A transaction flagged is_seed=True is demo data and must never contaminate
live money figures. These tests pin the two report surfaces called out in the
ticket:
  * X-report and Z-report totals exclude is_seed rows, and
  * the IngredientLog stock-movement feed excludes is_seed rows.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User, PosTransaction,
    IngredientUnit, Ingredient, RecipeIngredient,
)
from canteen.serializers import ZReportSerializer
from canteen.services import (
    create_pos_transaction, close_shift_and_finalize_z,
    stock_movements_for_shift,
)


class SeedQuarantineTests(APITestCase):
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

    def _sell(self, qty):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': qty}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )

    def _seed_sale(self, qty):
        """Ring a sale through the normal path (so it gets frozen totals and a
        ledger row bound to this shift), then quarantine it as seed data."""
        txn = self._sell(qty)
        PosTransaction.objects.filter(pk=txn.pk).update(is_seed=True)
        return PosTransaction.objects.get(pk=txn.pk)

    def test_is_seed_excluded_from_xreport_totals(self):
        self._sell(2)        # real: 2 * 50 = 100.00
        self._seed_sale(3)   # seed: 150.00 — must not count
        self.client.force_authenticate(self.cashier)
        resp = self.client.get('/api/canteen/transactions/xreport/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['transaction_count'], 1)
        self.assertEqual(resp.data['gross_sales'], 100.0)

    def test_is_seed_excluded_from_zreport_totals(self):
        self._sell(2)        # real: 100.00
        self._seed_sale(4)   # seed: 200.00 — must not count
        z = close_shift_and_finalize_z(
            self.shift.id, Decimal('0.00'), self.cashier
        )
        self.assertEqual(z.transaction_count, 1)
        self.assertEqual(z.gross_sales, Decimal('100.00'))
        self.assertEqual(z.net_sales, Decimal('100.00'))

    def test_stock_movements_excludes_is_seed(self):
        self._sell(2)        # real: 4.0 sold
        self._seed_sale(5)   # seed: 10.0 — must not surface
        movements = stock_movements_for_shift(self.shift)
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0]['ingredient_name'], 'Beans')
        self.assertEqual(movements[0]['sold'], 4.0)

    def test_zreport_serializer_stock_movements_excludes_is_seed(self):
        self._sell(2)        # 4.0 sold
        self._seed_sale(5)   # 10.0 seed
        z = close_shift_and_finalize_z(
            self.shift.id, Decimal('0.00'), self.cashier
        )
        data = ZReportSerializer(z).data
        self.assertEqual(len(data['stock_movements']), 1)
        self.assertEqual(data['stock_movements'][0]['sold'], 4.0)
