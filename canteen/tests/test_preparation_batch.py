"""FEATURE-056 — sub-recipe / preparation (BOM) + "prep a batch".

A preparation is an Ingredient (is_preparation) built from other ingredients
(PreparationComponent). services.produce_batch():
  - deducts each component (quantity_used * num_batches),
  - adds batch_yield * num_batches to the prep's stock,
  - rolls the batch unit cost into the prep's weighted-average cost_per_unit.

These tests pin the money math: batch cost, weighted-average roll, cost roll-up
into a menu item, and — critically — that a raw material shared between a batch
and a direct drink recipe keeps ONE stock with NO double-count (components are
consumed at production; the prep is consumed once at sale).
"""

from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

from canteen.models import (
    BusinessProfile, User, IngredientUnit, Ingredient, PreparationComponent,
    Item, RecipeIngredient, Shift, IngredientLog,
)
from canteen import services


class PrepBase(TestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Cafe', currency='PHP', vat_enabled=False,
            track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier1', password='x', role='cashier')
        self.g, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})
        self.ml, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Millilitre'})
        # Raw materials
        self.sugar = Ingredient.objects.create(
            name='Sugar', unit=self.g, cost_per_unit=Decimal('0.0600'),
            current_stock=Decimal('10000'))
        self.water = Ingredient.objects.create(
            name='Water', unit=self.ml, cost_per_unit=Decimal('0.0010'),
            current_stock=Decimal('10000'))
        # Preparation: Simple Syrup, 1 batch yields 1000 ml
        self.syrup = Ingredient.objects.create(
            name='Simple Syrup', unit=self.ml, cost_per_unit=Decimal('0'),
            current_stock=Decimal('0'), is_preparation=True,
            batch_yield=Decimal('1000'))
        PreparationComponent.objects.create(
            preparation=self.syrup, component=self.sugar,
            quantity_used=Decimal('500'))
        PreparationComponent.objects.create(
            preparation=self.syrup, component=self.water,
            quantity_used=Decimal('500'))

    def _refresh(self):
        for o in (self.sugar, self.water, self.syrup):
            o.refresh_from_db()


class ProduceBatchMath(PrepBase):
    def test_single_batch_stock_and_cost(self):
        res = services.produce_batch(self.syrup, 1, performed_by=self.cashier)
        self._refresh()
        # batch cost = 500*0.06 + 500*0.001 = 30.5 ; unit = 30.5/1000 = 0.0305
        self.assertEqual(Decimal(res['batch_cost']), Decimal('30.5000'))
        self.assertEqual(Decimal(res['unit_cost']), Decimal('0.0305'))
        # prep gained a full batch at that unit cost (was empty -> adopt)
        self.assertEqual(self.syrup.current_stock, Decimal('1000'))
        self.assertEqual(self.syrup.cost_per_unit, Decimal('0.0305'))
        # components depleted exactly once
        self.assertEqual(self.sugar.current_stock, Decimal('9500'))
        self.assertEqual(self.water.current_stock, Decimal('9500'))
        # ledger: 2 component + 1 prep production rows
        prod = IngredientLog.objects.filter(action='production')
        self.assertEqual(prod.count(), 3)
        self.assertEqual(
            prod.get(ingredient=self.syrup).quantity_change, Decimal('1000'))
        self.assertEqual(
            prod.get(ingredient=self.sugar).quantity_change, Decimal('-500'))

    def test_weighted_average_roll_on_second_batch(self):
        services.produce_batch(self.syrup, 1)            # prep 1000 @ 0.0305
        # component price rises before the next batch
        self.sugar.cost_per_unit = Decimal('0.1200')
        self.sugar.save(update_fields=['cost_per_unit'])
        res = services.produce_batch(self.syrup, 1)      # batch2 unit 0.0605
        self._refresh()
        # batch2 unit = (500*0.12 + 500*0.001)/1000 = 60.5/1000 = 0.0605
        self.assertEqual(Decimal(res['unit_cost']), Decimal('0.0605'))
        # weighted avg: (1000*0.0305 + 1000*0.0605)/2000 = 91/2000 = 0.0455
        self.assertEqual(self.syrup.current_stock, Decimal('2000'))
        self.assertEqual(self.syrup.cost_per_unit, Decimal('0.0455'))

    def test_multiple_batches_scale(self):
        services.produce_batch(self.syrup, 3)
        self._refresh()
        self.assertEqual(self.syrup.current_stock, Decimal('3000'))
        self.assertEqual(self.sugar.current_stock, Decimal('10000') - Decimal('1500'))
        self.assertEqual(self.syrup.cost_per_unit, Decimal('0.0305'))


class CostRollupAndNoDoubleCount(PrepBase):
    def setUp(self):
        super().setUp()
        services.produce_batch(self.syrup, 1)  # syrup: 1000 ml @ 0.0305
        self._refresh()
        # Menu item using the preparation: 50 ml syrup per Lemonade
        self.lemonade = Item.objects.create(
            name='Lemonade', price=Decimal('60'),
            purchase_price=Decimal('0'), stock=100)
        RecipeIngredient.objects.create(
            item=self.lemonade, ingredient=self.syrup,
            quantity_used=Decimal('50'))
        Shift.objects.create(
            cashier=self.cashier, is_open=True, opening_cash=Decimal('1000'))

    def test_cost_rolls_up_into_item(self):
        # 50 ml * 0.0305 = 1.5250
        self.assertEqual(services.item_recipe_cost(self.lemonade), Decimal('1.5250'))

    def test_sale_depletes_prep_once_not_components(self):
        services.create_pos_transaction(
            [{'item_id': str(self.lemonade.id), 'quantity': 2}],
            'cash', cashier=self.cashier, cash_received='9999')
        self._refresh()
        # prep down 2*50 = 100 ; components UNCHANGED by the sale (already
        # consumed at production — no double-count)
        self.assertEqual(self.syrup.current_stock, Decimal('900'))
        self.assertEqual(self.sugar.current_stock, Decimal('9500'))
        self.assertEqual(self.water.current_stock, Decimal('9500'))

    def test_makeable_uses_prep_onhand(self):
        # 900 ml on hand / 50 per drink -> 18 makeable (after the 2-drink sale)
        services.create_pos_transaction(
            [{'item_id': str(self.lemonade.id), 'quantity': 2}],
            'cash', cashier=self.cashier, cash_received='9999')
        self.lemonade.refresh_from_db()
        makeable, status = services.item_makeable(self.lemonade)
        self.assertEqual(status, 'ok')
        self.assertEqual(makeable, 18)


class SharedRawMaterial(PrepBase):
    def test_one_stock_shared_between_batch_and_direct_recipe(self):
        # Sugar is BOTH a syrup component and a direct ingredient of Cookie.
        cookie = Item.objects.create(
            name='Cookie', price=Decimal('40'), purchase_price=Decimal('0'),
            stock=100)
        RecipeIngredient.objects.create(
            item=cookie, ingredient=self.sugar, quantity_used=Decimal('20'))
        Shift.objects.create(
            cashier=self.cashier, is_open=True, opening_cash=Decimal('1000'))
        services.produce_batch(self.syrup, 1)             # -500 sugar
        services.create_pos_transaction(
            [{'item_id': str(cookie.id), 'quantity': 3}],
            'cash', cashier=self.cashier, cash_received='9999')  # -60 sugar
        self.sugar.refresh_from_db()
        # single row decremented by both paths: 10000 - 500 - 60 = 9440
        self.assertEqual(self.sugar.current_stock, Decimal('9440'))


class Guards(PrepBase):
    def test_non_preparation_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.produce_batch(self.sugar, 1)

    def test_zero_batches_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.produce_batch(self.syrup, 0)

    def test_missing_yield_rejected(self):
        self.syrup.batch_yield = None
        self.syrup.save(update_fields=['batch_yield'])
        with self.assertRaises(DRFValidationError):
            services.produce_batch(self.syrup, 1)

    def test_no_components_rejected(self):
        PreparationComponent.objects.filter(preparation=self.syrup).delete()
        with self.assertRaises(DRFValidationError):
            services.produce_batch(self.syrup, 1)

    def test_preparation_cannot_contain_itself(self):
        with self.assertRaises(IntegrityError):
            PreparationComponent.objects.create(
                preparation=self.syrup, component=self.syrup,
                quantity_used=Decimal('1'))
