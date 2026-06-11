"""ISSUE-113 — recipe/ingredient unit labeling and quantity validation.

Field report (Demo Cafe): per-serving quantities entered in the recipe
builder are silently interpreted in the ingredient's base unit, and several
inputs/labels never said which unit they expect. The audit ruled out any
implicit g<->kg conversion — quantities flow ingredient unit -> recipe
quantity_used -> depletion math unchanged. These tests pin that down:

  - the exact reported scenario: 60 g stock, 12 g/serving, one sale -> 48 g,
  - the API rejects zero/negative quantity_used (recipe) and quantity_added
    (restock) so depletion can't be silently disabled or inverted,
  - the serializers expose the unit data (unit_detail.abbreviation) the
    frontend unit labels render from, including nested on recipe rows.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User,
    IngredientUnit, Ingredient, RecipeIngredient,
)
from canteen.services import create_pos_transaction


class RecipeUnitsBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        self.gram, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'}
        )
        self.powder = Ingredient.objects.create(
            name='Coffee Caramel Powder', unit=self.gram,
            cost_per_unit=Decimal('2.5000'),
            current_stock=Decimal('60.0000'),
        )
        self.item = Item.objects.create(
            name='Caramel Latte', price=Decimal('120.00'), stock=100
        )


class ReportedDepletionScenarioTests(RecipeUnitsBase):
    """The exact field-report math: 5 scoops weighed = 60 g stock, one serving
    uses 12 g (one scoop), one sale must leave 48 g — no unit conversion."""

    def test_one_sale_depletes_one_serving_in_grams(self):
        RecipeIngredient.objects.create(
            item=self.item, ingredient=self.powder,
            quantity_used=Decimal('12.0000'),
        )
        create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )
        self.powder.refresh_from_db()
        self.assertEqual(self.powder.current_stock, Decimal('48.0000'))


class RecipeQuantityValidationTests(RecipeUnitsBase):
    def _post_recipe(self, qty):
        self.client.force_authenticate(self.manager)
        return self.client.post(
            '/api/canteen/recipe-ingredients/',
            {'item': self.item.id, 'ingredient': self.powder.id,
             'quantity_used': qty},
            format='json',
        )

    def test_rejects_zero_quantity(self):
        resp = self._post_recipe('0')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('quantity_used', resp.data)

    def test_rejects_negative_quantity(self):
        resp = self._post_recipe('-12')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('quantity_used', resp.data)

    def test_accepts_positive_quantity(self):
        resp = self._post_recipe('12.0000')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(RecipeIngredient.objects.count(), 1)


class RestockQuantityValidationTests(RecipeUnitsBase):
    def _restock(self, qty):
        self.client.force_authenticate(self.manager)
        return self.client.post(
            f'/api/canteen/ingredients/{self.powder.id}/restock/',
            {'quantity_added': qty, 'cost_per_unit': '2.50'},
            format='json',
        )

    def test_rejects_zero_quantity(self):
        resp = self._restock('0')
        self.assertEqual(resp.status_code, 400)
        self.powder.refresh_from_db()
        self.assertEqual(self.powder.current_stock, Decimal('60.0000'))

    def test_rejects_negative_quantity(self):
        resp = self._restock('-10')
        self.assertEqual(resp.status_code, 400)
        self.powder.refresh_from_db()
        self.assertEqual(self.powder.current_stock, Decimal('60.0000'))

    def test_accepts_positive_quantity(self):
        resp = self._restock('100.0000')
        self.assertEqual(resp.status_code, 201)
        self.powder.refresh_from_db()
        self.assertEqual(self.powder.current_stock, Decimal('160.0000'))


class UnitLabelRenderingDataTests(RecipeUnitsBase):
    """The frontend unit labels (recipe qty suffix, stock/par/cost labels,
    restock modal) render from unit_detail.abbreviation — assert the API
    actually serves it, both top-level and nested in recipe rows."""

    def test_ingredient_serializer_exposes_unit_detail(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.get(f'/api/canteen/ingredients/{self.powder.id}/')
        self.assertEqual(resp.status_code, 200)
        # The labels render the abbreviation; name may vary (seeded vs preset).
        self.assertEqual(resp.data['unit_detail']['abbreviation'], 'g')
        self.assertTrue(resp.data['unit_detail']['name'])

    def test_recipe_ingredient_nests_unit_detail(self):
        RecipeIngredient.objects.create(
            item=self.item, ingredient=self.powder,
            quantity_used=Decimal('12.0000'),
        )
        self.client.force_authenticate(self.manager)
        resp = self.client.get(
            f'/api/canteen/recipe-ingredients/?item={self.item.id}'
        )
        self.assertEqual(resp.status_code, 200)
        rows = resp.data if isinstance(resp.data, list) else resp.data['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]['ingredient_detail']['unit_detail']['abbreviation'], 'g'
        )
