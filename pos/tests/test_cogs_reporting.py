"""FEATURE-054 — recipe-valued COGS, gross profit/margin, and the enriched
weekly report (profit-by-item, ingredient reorder list).

COGS uses the cost-at-sale snapshot (PosTransactionItem.unit_cost, option B):
the recipe-derived cost for recipe items, else the item's manual purchase_price,
frozen at sale time so reporting stays accurate as ingredient costs drift. This
is a profitability view, kept distinct from Net Cash Flow (a cash-basis figure).
"""

from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, User, IngredientUnit, Ingredient, Item, RecipeIngredient,
    Shift,
)
from pos.services import create_pos_transaction
from pos.views import (
    _weekly_cogs, _weekly_profit_by_item, _ingredient_reorder_list,
)


class CogsBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP',
            vat_enabled=False, track_inventory=False, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0'), is_open=True
        )
        self.g, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'}
        )
        self.beans = Ingredient.objects.create(
            name='Beans', unit=self.g, cost_per_unit=Decimal('0.5000'),
            current_stock=Decimal('10000'),
        )
        self.milk = Ingredient.objects.create(
            name='Milk', unit=self.g, cost_per_unit=Decimal('0.1000'),
            current_stock=Decimal('10000'),
        )
        # Recipe item: 12 g beans (6.00) + 20 g milk (2.00) = 8.00 cost.
        self.latte = Item.objects.create(
            name='Latte', price=Decimal('120.00'), purchase_price=Decimal('0'),
            stock=1000,
        )
        RecipeIngredient.objects.create(
            item=self.latte, ingredient=self.beans, quantity_used=Decimal('12'),
        )
        RecipeIngredient.objects.create(
            item=self.latte, ingredient=self.milk, quantity_used=Decimal('20'),
        )
        # Resale item: manual cost 15.00.
        self.water = Item.objects.create(
            name='Bottled Water', price=Decimal('25.00'),
            purchase_price=Decimal('15.00'), stock=1000,
        )

    def _sell(self, item, qty=1):
        return create_pos_transaction(
            [{'item_id': item.id, 'quantity': qty}], 'cash',
            cashier=self.cashier, cash_received=Decimal('5000'),
        )


class UnitCostSnapshotTests(CogsBase):
    def test_recipe_item_snapshots_derived_cost(self):
        txn = self._sell(self.latte, 1)
        line = txn.items.first()
        self.assertEqual(line.unit_cost, Decimal('8.0000'))

    def test_resale_item_snapshots_manual_purchase_price(self):
        txn = self._sell(self.water, 1)
        line = txn.items.first()
        self.assertEqual(line.unit_cost, Decimal('15.0000'))


class WeeklyCogsTests(CogsBase):
    def test_cogs_sums_snapshots_over_window(self):
        self._sell(self.latte, 2)   # 8.00 * 2 = 16
        self._sell(self.water, 1)   # 15.00 * 1 = 15
        today = timezone.localdate()
        cogs, costed, total = _weekly_cogs(today, today)
        self.assertEqual(cogs, Decimal('31'))
        self.assertEqual(costed, total)  # every line has a snapshot
        self.assertEqual(total, 2)

    def test_profit_by_item_ranks_by_gross_profit(self):
        self._sell(self.latte, 2)   # revenue 240, cost 16, profit 224
        self._sell(self.water, 1)   # revenue 25, cost 15, profit 10
        today = timezone.localdate()
        rows = _weekly_profit_by_item(today, today)
        self.assertEqual(rows[0]['name'], 'Latte')
        self.assertEqual(Decimal(rows[0]['profit']), Decimal('224.00'))
        self.assertEqual(Decimal(rows[0]['cost']), Decimal('16.00'))
        water = next(r for r in rows if r['name'] == 'Bottled Water')
        self.assertEqual(Decimal(water['profit']), Decimal('10.00'))


class IngredientReorderTests(CogsBase):
    def test_lists_only_ingredients_at_or_below_a_set_par(self):
        # beans/milk have par 0 (default) → excluded even though "at" par.
        low = Ingredient.objects.create(
            name='Cups', unit=self.g, cost_per_unit=Decimal('1'),
            current_stock=Decimal('5'), par_level=Decimal('20'),
        )
        Ingredient.objects.create(
            name='Lids', unit=self.g, cost_per_unit=Decimal('1'),
            current_stock=Decimal('500'), par_level=Decimal('20'),  # well stocked
        )
        names = [r['name'] for r in _ingredient_reorder_list()]
        self.assertIn('Cups', names)
        self.assertNotIn('Lids', names)
        self.assertNotIn('Beans', names)  # par 0, not a reorder signal
