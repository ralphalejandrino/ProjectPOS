"""FEATURE-052 — recipe-derived item cost + live margin.

A recipe item's per-unit cost is derived from its ingredients (each at its
weighted-average cost, FEATURE-051), not the manual purchase_price. The manual
cost is used only for pure resale items (no recipe). The API exposes:

  - recipe_cost           derived cost, or null for a resale item
  - is_recipe_item        has >=1 direct recipe line
  - effective_cost        recipe_cost for recipe items, else purchase_price
  - effective_margin      price - effective_cost (peso)
  - effective_margin_pct  true margin off price

Only DIRECT (variant-null) recipe lines feed the base cost; variant-scoped
lines do not. Cost tracks ingredient cost changes with no item edit.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, User, IngredientUnit, Ingredient, Item, RecipeIngredient,
    VariantGroup, VariantOption, ProductVariantGroup,
)


class DerivedCostBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.g, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'}
        )
        self.ml, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Millilitre'}
        )
        self.beans = Ingredient.objects.create(
            name='Beans', unit=self.g, cost_per_unit=Decimal('0.0500'),
            current_stock=Decimal('1000'),
        )
        self.milk = Ingredient.objects.create(
            name='Milk', unit=self.ml, cost_per_unit=Decimal('0.1000'),
            current_stock=Decimal('1000'),
        )
        self.client.force_authenticate(self.manager)

    def _get(self, item):
        resp = self.client.get(f'/api/pos/items/{item.id}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data


class RecipeItemCostTests(DerivedCostBase):
    def setUp(self):
        super().setUp()
        self.latte = Item.objects.create(
            name='Latte', price=Decimal('120.00'), purchase_price=Decimal('99.00'),
            stock=0,
        )
        RecipeIngredient.objects.create(
            item=self.latte, ingredient=self.beans, quantity_used=Decimal('12'),
        )
        RecipeIngredient.objects.create(
            item=self.latte, ingredient=self.milk, quantity_used=Decimal('20'),
        )

    def test_recipe_cost_is_sum_of_ingredient_costs(self):
        data = self._get(self.latte)
        # 12*0.05 + 20*0.10 = 0.60 + 2.00 = 2.60
        self.assertEqual(Decimal(data['recipe_cost']), Decimal('2.6000'))
        self.assertTrue(data['is_recipe_item'])

    def test_effective_cost_ignores_manual_purchase_price(self):
        data = self._get(self.latte)
        # recipe item -> derived, NOT the manual 99.00
        self.assertEqual(Decimal(data['effective_cost']), Decimal('2.6000'))

    def test_effective_margin_and_pct(self):
        data = self._get(self.latte)
        # 120 - 2.60 = 117.40 ; 117.40/120*100 = 97.833... -> 97.8
        self.assertEqual(Decimal(data['effective_margin']), Decimal('117.40'))
        self.assertAlmostEqual(data['effective_margin_pct'], 97.8, places=1)

    def test_cost_tracks_ingredient_cost_change(self):
        self.beans.cost_per_unit = Decimal('0.1000')
        self.beans.save(update_fields=['cost_per_unit'])
        data = self._get(self.latte)
        # 12*0.10 + 20*0.10 = 1.20 + 2.00 = 3.20
        self.assertEqual(Decimal(data['recipe_cost']), Decimal('3.2000'))

    def test_variant_line_excluded_from_base_cost(self):
        group = VariantGroup.objects.create(
            name='Size', selection_type='single', is_required=False
        )
        opt = VariantOption.objects.create(
            group=group, name='Large', price_modifier=Decimal('0')
        )
        ProductVariantGroup.objects.create(
            product=self.latte, group=group, enabled=True
        )
        RecipeIngredient.objects.create(
            item=self.latte, variant=opt, ingredient=self.beans,
            quantity_used=Decimal('99'),
        )
        data = self._get(self.latte)
        # variant-scoped line (99g) must NOT inflate the base cost.
        self.assertEqual(Decimal(data['recipe_cost']), Decimal('2.6000'))


class ResaleItemCostTests(DerivedCostBase):
    def test_resale_item_uses_manual_purchase_price(self):
        bottled = Item.objects.create(
            name='Bottled Water', price=Decimal('25.00'),
            purchase_price=Decimal('15.00'), stock=50,
        )
        data = self._get(bottled)
        self.assertIsNone(data['recipe_cost'])
        self.assertFalse(data['is_recipe_item'])
        self.assertEqual(Decimal(data['effective_cost']), Decimal('15.00'))
        self.assertEqual(Decimal(data['effective_margin']), Decimal('10.00'))

    def test_zero_price_margin_pct_is_null(self):
        freebie = Item.objects.create(
            name='Free Sample', price=Decimal('0.00'),
            purchase_price=Decimal('5.00'), stock=10,
        )
        data = self._get(freebie)
        self.assertIsNone(data['effective_margin_pct'])
