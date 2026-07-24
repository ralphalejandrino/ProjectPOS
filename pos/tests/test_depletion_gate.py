"""#3 depletion fix — ingredient depletion for RECIPE items is gated on
ingredient management, NOT on track_inventory.

Regression guard for the live PROD bug: track_inventory was OFF, which (because
the two were coupled under one flag) silently disabled ALL sale depletion —
0 of 95 real sales moved any ingredient. These tests pin the decoupling:
a recipe sale always depletes its ingredients when ingredient management is on,
regardless of track_inventory, and a recipe item is never gated on / decremented
from item.stock.
"""
from decimal import Decimal

from django.test import TestCase

from pos.models import (
    BusinessProfile, Item, Ingredient, IngredientUnit, RecipeIngredient,
    Shift, User, IngredientLog,
)
from pos.services import create_pos_transaction, refund_transaction


class DepletionGateTests(TestCase):
    def _setup(self, track_inventory, ingredient_mgmt=True):
        BusinessProfile.objects.create(
            business_name='PROD', currency='PHP', vat_enabled=False,
            track_inventory=track_inventory,
            ingredient_management_enabled=ingredient_mgmt,
            printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='c', password='x', role='cashier')
        Shift.objects.create(cashier=self.cashier,
                             opening_cash=Decimal('0.00'), is_open=True)
        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'})

    def _recipe_item(self, ing_stock='100'):
        item = Item.objects.create(name='Latte', price=Decimal('59.00'), stock=0)
        ing = Ingredient.objects.create(
            name='Milk', unit=self.unit, cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal(ing_stock))
        RecipeIngredient.objects.create(item=item, ingredient=ing,
                                        quantity_used=Decimal('10.0000'))
        return item, ing

    def test_recipe_sale_depletes_even_with_track_inventory_off(self):
        """THE PROD bug: track_inventory off must NOT disable depletion."""
        self._setup(track_inventory=False)
        item, ing = self._recipe_item(ing_stock='100')

        txn = create_pos_transaction(
            [{'item_id': item.id, 'quantity': 2}], 'cash',
            cashier=self.cashier, cash_received=Decimal('200.00'))

        self.assertIsNotNone(txn)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('80.0000'))   # 100 - 10*2
        self.assertEqual(
            IngredientLog.objects.filter(
                ingredient=ing, action='sale', transaction=txn).count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.stock, 0)                           # untouched

    def test_no_depletion_when_ingredient_mgmt_off(self):
        """Ingredient management OFF → a recipe sale does not deplete."""
        self._setup(track_inventory=False, ingredient_mgmt=False)
        item, ing = self._recipe_item(ing_stock='100')
        create_pos_transaction(
            [{'item_id': item.id, 'quantity': 1}], 'cash',
            cashier=self.cashier, cash_received=Decimal('100.00'))
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('100.0000'))
        self.assertEqual(IngredientLog.objects.filter(action='sale').count(), 0)

    def test_resale_item_not_decremented_when_track_inventory_off(self):
        """A pure resale (no-recipe) item: no depletion; with track_inventory
        off its item.stock is not decremented (unchanged behaviour)."""
        self._setup(track_inventory=False)
        item = Item.objects.create(name='Bottled Water',
                                   price=Decimal('20.00'), stock=5)
        create_pos_transaction(
            [{'item_id': item.id, 'quantity': 1}], 'cash',
            cashier=self.cashier, cash_received=Decimal('20.00'))
        item.refresh_from_db()
        self.assertEqual(item.stock, 5)
        self.assertEqual(IngredientLog.objects.count(), 0)

    def test_resale_item_decrements_when_track_inventory_on(self):
        """A resale item still decrements item.stock when track_inventory is on."""
        self._setup(track_inventory=True)
        item = Item.objects.create(name='Bottled Water',
                                   price=Decimal('20.00'), stock=5)
        create_pos_transaction(
            [{'item_id': item.id, 'quantity': 2}], 'cash',
            cashier=self.cashier, cash_received=Decimal('40.00'))
        item.refresh_from_db()
        self.assertEqual(item.stock, 3)

    def test_refund_restores_ingredient_with_track_inventory_off(self):
        """Refund restores ingredient stock (symmetry) even with track_inv off."""
        self._setup(track_inventory=False)
        item, ing = self._recipe_item(ing_stock='100')
        txn = create_pos_transaction(
            [{'item_id': item.id, 'quantity': 2}], 'cash',
            cashier=self.cashier, cash_received=Decimal('200.00'))
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('80.0000'))
        refund_transaction(txn.id, self.cashier)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('100.0000'))  # restored
