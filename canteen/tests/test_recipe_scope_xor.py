"""BUG-001 — recipe-ingredient scope XOR is validated at the serializer.

A RecipeIngredient row is scoped to EXACTLY one of `item` or `variant`; the
model enforces this with the `recipe_item_or_variant_not_both` CheckConstraint.
Before this fix the serializer never validated the XOR, so a payload with both
(or neither) reached the DB and surfaced as an uncaught IntegrityError → HTTP
500. The serializer must reject those with a clean 400 instead, while valid
item-only and variant-only payloads still create (201).
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, ItemCategory, User,
    VariantGroup, VariantOption, CategoryVariantGroup,
    IngredientUnit, Ingredient, RecipeIngredient,
)


class RecipeScopeXorTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP', printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.client.force_authenticate(self.manager)

        self.cat = ItemCategory.objects.create(name='Drinks')
        self.item = Item.objects.create(
            name='Milk Tea', price=Decimal('100.00'), stock=100, category=self.cat
        )
        self.size = VariantGroup.objects.create(name='Size', sort_order=0)
        self.opt_size = VariantOption.objects.create(group=self.size, name='Large')
        CategoryVariantGroup.objects.create(category=self.cat, group=self.size)

        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Milliliters'}
        )
        self.ing = Ingredient.objects.create(
            name='Whole Milk', unit=self.unit, cost_per_unit=Decimal('0.0600'),
            current_stock=Decimal('1000.0000'),
        )

    def _post(self, payload):
        return self.client.post(
            '/api/canteen/recipe-ingredients/', payload, format='json',
        )

    def test_both_item_and_variant_is_rejected_400_not_500(self):
        resp = self._post({
            'item': str(self.item.id), 'variant': str(self.opt_size.id),
            'ingredient': str(self.ing.id), 'quantity_used': '50.0',
        })
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(RecipeIngredient.objects.count(), 0)

    def test_neither_item_nor_variant_is_rejected_400(self):
        resp = self._post({
            'ingredient': str(self.ing.id), 'quantity_used': '50.0',
        })
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(RecipeIngredient.objects.count(), 0)

    def test_variant_only_is_created_201(self):
        resp = self._post({
            'item': None, 'variant': str(self.opt_size.id),
            'ingredient': str(self.ing.id), 'quantity_used': '50.0',
        })
        self.assertEqual(
            resp.status_code, status.HTTP_201_CREATED, resp.data,
        )
        row = RecipeIngredient.objects.get()
        self.assertIsNone(row.item)
        self.assertEqual(row.variant_id, self.opt_size.id)

    def test_item_only_is_created_201(self):
        resp = self._post({
            'item': str(self.item.id), 'variant': None,
            'ingredient': str(self.ing.id), 'quantity_used': '50.0',
        })
        self.assertEqual(
            resp.status_code, status.HTTP_201_CREATED, resp.data,
        )
        row = RecipeIngredient.objects.get()
        self.assertEqual(row.item_id, self.item.id)
        self.assertIsNone(row.variant)

    # ── Read-side scope contract (BUG-001 list fix) ──────────────────────
    # The recipe editor lists lines by the SAME scope it writes them with:
    # ?variant=<id> when a variant is selected, ?item=<id> otherwise. These
    # lock the backend filter the frontend now relies on, so an item-scoped
    # GET never silently hides variant-scoped lines (or vice versa).

    def test_list_by_variant_returns_only_that_variants_lines(self):
        item_line = RecipeIngredient.objects.create(
            item=self.item, ingredient=self.ing, quantity_used=Decimal('10.0'),
        )
        variant_line = RecipeIngredient.objects.create(
            variant=self.opt_size, ingredient=self.ing, quantity_used=Decimal('20.0'),
        )
        resp = self.client.get(
            f'/api/canteen/recipe-ingredients/?variant={self.opt_size.id}'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        rows = resp.data['results'] if isinstance(resp.data, dict) else resp.data
        ids = {r['id'] for r in rows}
        self.assertEqual(ids, {variant_line.id})
        self.assertNotIn(item_line.id, ids)

    def test_list_by_item_returns_only_item_level_lines(self):
        item_line = RecipeIngredient.objects.create(
            item=self.item, ingredient=self.ing, quantity_used=Decimal('10.0'),
        )
        RecipeIngredient.objects.create(
            variant=self.opt_size, ingredient=self.ing, quantity_used=Decimal('20.0'),
        )
        resp = self.client.get(
            f'/api/canteen/recipe-ingredients/?item={self.item.id}'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        rows = resp.data['results'] if isinstance(resp.data, dict) else resp.data
        ids = {r['id'] for r in rows}
        self.assertEqual(ids, {item_line.id})
