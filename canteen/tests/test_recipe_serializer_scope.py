"""BUG-014 regression test: serializer item/variant scope rules.

BUG-013 (migration 0043) replaced the model's item/variant XOR with
item-required/variant-optional. This test verifies the serializer's validate()
matches that contract — specifically that (item AND variant) is accepted,
not rejected with the old BUG-001 XOR error.

Three scenarios the manager's flow exercises:
1. Variant-scoped line: item + variant → must succeed (201)
2. Base line: item + variant=null → must succeed (201)
3. Missing item: no item → must fail with clear error
"""

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, ItemCategory, User,
    VariantGroup, VariantOption, ProductVariantGroup,
    IngredientUnit, Ingredient,
)


class RecipeIngredientScopeRegressionTests(APITestCase):
    """Verifies the serializer accepts (item AND variant) together — the
    manager's literal 'Four Season Tea Syrup on Fruit Tea - Four Season' case
    that the old BUG-001 XOR rejected."""

    def setUp(self):
        BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP', vat_enabled=False,
            track_inventory=True, printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.client.force_authenticate(self.manager)

        cat = ItemCategory.objects.create(name='Fruit Teas')
        self.item = Item.objects.create(
            name='Fruit Tea - Four Season', price=100.00,
            stock=1000, category=cat,
        )

        self.group = VariantGroup.objects.create(name='Flavor', max_selections=1)
        ProductVariantGroup.objects.create(product=self.item, group=self.group, enabled=True)
        self.option = VariantOption.objects.create(
            group=self.group, name='Four Season', price_modifier=0,
        )

        unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Milliliters'}
        )
        self.ingredient = Ingredient.objects.create(
            name='Four Season Tea Syrup', unit=unit,
            cost_per_unit=0.10, current_stock=1000,
        )

    # ── Scenario 1: the manager's literal failing case ──────────────────

    def test_variant_scoped_ingredient_saves_through_api(self):
        """POST item + variant = 201 (manager's exact flow, no XOR error)."""
        resp = self.client.post('/api/canteen/recipe-ingredients/', {
            'item': str(self.item.id),
            'variant': str(self.option.id),
            'ingredient': str(self.ingredient.id),
            'quantity_used': '5.0',
            'depletion_mode': 'replace',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        # Confirm the saved row carries both FK values
        self.assertEqual(str(resp.data['item']), str(self.item.id))
        self.assertEqual(str(resp.data['variant']), str(self.option.id))

    # ── Scenario 2: base line ──────────────────────────────────────────

    def test_base_item_line_saves_through_api(self):
        """POST item + variant=null = 201 (base recipe line, no regression)."""
        resp = self.client.post('/api/canteen/recipe-ingredients/', {
            'item': str(self.item.id),
            'variant': None,
            'ingredient': str(self.ingredient.id),
            'quantity_used': '3.0',
            'depletion_mode': 'replace',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNone(resp.data['variant'])

    # ── Scenario 3: missing item ───────────────────────────────────────

    def test_missing_item_returns_400_with_clear_message(self):
        """POST without item = 400, not 500. Message says 'belong to an item'."""
        resp = self.client.post('/api/canteen/recipe-ingredients/', {
            'variant': str(self.option.id),
            'ingredient': str(self.ingredient.id),
            'quantity_used': '1.0',
            'depletion_mode': 'replace',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        err = resp.data.get('non_field_errors', [])
        self.assertTrue(len(err) > 0, resp.data)
        msg = str(err[0])
        self.assertIn('belong to an "item"', msg)
        # Confirm the OLD XOR message is NOT what fired
        self.assertNotIn('exactly one', msg)

    # ── Scenario 4: update keeps existing value when field omitted ─────

    def test_update_preserves_existing_item_and_variant(self):
        """PATCH omitting item uses instance value (not required on every
        write — the serializer falls back to self.instance)."""
        created = self.client.post('/api/canteen/recipe-ingredients/', {
            'item': str(self.item.id),
            'variant': str(self.option.id),
            'ingredient': str(self.ingredient.id),
            'quantity_used': '5.0',
            'depletion_mode': 'replace',
        }, format='json')
        pk = created.data['id']

        resp = self.client.patch(f'/api/canteen/recipe-ingredients/{pk}/', {
            'quantity_used': '8.0',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['quantity_used'], '8.0000')
        # item and variant unchanged from the original
        self.assertEqual(str(resp.data['item']), str(self.item.id))
        self.assertEqual(str(resp.data['variant']), str(self.option.id))
