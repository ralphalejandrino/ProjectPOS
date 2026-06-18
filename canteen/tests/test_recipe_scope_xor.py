"""Recipe-ingredient scope is validated at the serializer.

BUG-013 supersedes BUG-001's item/variant XOR: every RecipeIngredient row is now
owned by an `item` (the `recipe_item_required` CheckConstraint). A base line is
item-only (`variant` null); a variant line carries BOTH item and variant so it
stays scoped to that one item — the VariantOption is shared across products, and
the old XOR forced `item` NULL on variant lines, attaching them to the shared
option and bleeding them across items.

The serializer must reject a payload with no item with a clean 400 (not an
uncaught IntegrityError → 500), while item-only and item+variant payloads create
(201). The cross-item isolation guarantee itself is covered by
test_recipe_variant_isolation.py (the BUG-013 regression test).
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

    def test_item_and_variant_together_is_created_201(self):
        # BUG-013: a variant line now carries BOTH item and variant (was a 400
        # under BUG-001's XOR). This is the per-item variant recipe.
        resp = self._post({
            'item': str(self.item.id), 'variant': str(self.opt_size.id),
            'ingredient': str(self.ing.id), 'quantity_used': '50.0',
        })
        self.assertEqual(
            resp.status_code, status.HTTP_201_CREATED,
            f"Expected 201, got {resp.status_code}: {resp.data}",
        )
        row = RecipeIngredient.objects.get()
        self.assertEqual(row.item_id, self.item.id)
        self.assertEqual(row.variant_id, self.opt_size.id)

    def test_neither_item_nor_variant_is_rejected_400(self):
        resp = self._post({
            'ingredient': str(self.ing.id), 'quantity_used': '50.0',
        })
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(RecipeIngredient.objects.count(), 0)

    def test_variant_without_item_is_rejected_400(self):
        # BUG-013: item is mandatory — a variant line with item NULL is what
        # bled across items, so it must now be rejected (was 201 under BUG-001).
        resp = self._post({
            'item': None, 'variant': str(self.opt_size.id),
            'ingredient': str(self.ing.id), 'quantity_used': '50.0',
        })
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(RecipeIngredient.objects.count(), 0)

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

    # ── Read-side scope contract (BUG-013) ───────────────────────────────
    # The recipe editor lists lines by the SAME (item, variant) scope it writes
    # with: ?item=<id>&variant=<id> when a variant is selected, ?item=<id>
    # otherwise. These lock the backend filter the frontend relies on so the
    # base view never shows variant lines, and the variant view returns only
    # THIS item's lines for that option.

    def test_list_by_item_and_variant_returns_only_that_lines(self):
        base_line = RecipeIngredient.objects.create(
            item=self.item, ingredient=self.ing, quantity_used=Decimal('10.0'),
        )
        variant_line = RecipeIngredient.objects.create(
            item=self.item, variant=self.opt_size, ingredient=self.ing,
            quantity_used=Decimal('20.0'),
        )
        resp = self.client.get(
            f'/api/canteen/recipe-ingredients/'
            f'?item={self.item.id}&variant={self.opt_size.id}'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        rows = resp.data['results'] if isinstance(resp.data, dict) else resp.data
        ids = {r['id'] for r in rows}
        self.assertEqual(ids, {variant_line.id})
        self.assertNotIn(base_line.id, ids)

    def test_list_by_item_returns_only_base_lines(self):
        # A bare ?item=<id> (base-recipe view) must exclude the item's variant
        # lines — they now carry item too, so an unfiltered item query would
        # leak them into the base view.
        base_line = RecipeIngredient.objects.create(
            item=self.item, ingredient=self.ing, quantity_used=Decimal('10.0'),
        )
        RecipeIngredient.objects.create(
            item=self.item, variant=self.opt_size, ingredient=self.ing,
            quantity_used=Decimal('20.0'),
        )
        resp = self.client.get(
            f'/api/canteen/recipe-ingredients/?item={self.item.id}'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        rows = resp.data['results'] if isinstance(resp.data, dict) else resp.data
        ids = {r['id'] for r in rows}
        self.assertEqual(ids, {base_line.id})
