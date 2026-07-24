"""FLAG-050 — variant-variant ingredient overlap is rejected at authoring time.

When two variant groups effective on the same item both contribute the same
ingredient, depletion silently sums the quantities at sale time. Authoring the
second overlapping variant recipe must be rejected with a validation error.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, Item, ItemCategory, User,
    VariantGroup, VariantOption, CategoryVariantGroup,
    IngredientUnit, Ingredient, RecipeIngredient,
)


class VariantIngredientOverlapTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Pos', currency='PHP', printer_mode='disabled',
        )
        # 'ingredients' page defaults to manager/admin.
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.client.force_authenticate(self.manager)

        self.cat = ItemCategory.objects.create(name='Drinks')
        self.item = Item.objects.create(
            name='Milk Tea', price=Decimal('100.00'), stock=100, category=self.cat
        )
        # Two groups effective on the item via the category.
        self.size = VariantGroup.objects.create(name='Size', sort_order=0)
        self.opt_size = VariantOption.objects.create(group=self.size, name='Large')
        self.milk = VariantGroup.objects.create(name='Milk', sort_order=1)
        self.opt_milk = VariantOption.objects.create(group=self.milk, name='Oat')
        CategoryVariantGroup.objects.create(category=self.cat, group=self.size)
        CategoryVariantGroup.objects.create(category=self.cat, group=self.milk)

        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Milliliters'}
        )
        self.milk_ing = Ingredient.objects.create(
            name='Whole Milk', unit=self.unit, cost_per_unit=Decimal('0.0600'),
            current_stock=Decimal('1000.0000'),
        )

    def _post_recipe(self, variant, ingredient, qty='50.0'):
        # BUG-013: a variant line is owned by its item (recipe_item_required);
        # the FLAG-050 cross-group overlap check is group-based and unaffected.
        return self.client.post(
            '/api/pos/recipe-ingredients/',
            {'item': str(self.item.id), 'variant': str(variant.id),
             'ingredient': str(ingredient.id), 'quantity_used': qty},
            format='json',
        )

    def test_overlapping_ingredient_across_groups_is_rejected(self):
        # First variant recipe authors fine.
        resp1 = self._post_recipe(self.opt_size, self.milk_ing)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED, resp1.data)
        # Second group sharing the same ingredient → rejected.
        resp2 = self._post_recipe(self.opt_milk, self.milk_ing)
        self.assertEqual(
            resp2.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp2.status_code}: {resp2.data}",
        )
        self.assertEqual(
            RecipeIngredient.objects.filter(ingredient=self.milk_ing).count(), 1
        )

    def test_same_group_different_option_is_allowed(self):
        # Two options within the SAME group never co-occur on a single-select
        # sale the way two groups do; sharing an ingredient there is fine.
        opt_size_small = VariantOption.objects.create(group=self.size, name='Small')
        self.assertEqual(
            self._post_recipe(self.opt_size, self.milk_ing).status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            self._post_recipe(opt_size_small, self.milk_ing).status_code,
            status.HTTP_201_CREATED,
        )

    def test_non_overlapping_ingredient_is_allowed(self):
        other = Ingredient.objects.create(
            name='Espresso Beans', unit=self.unit, cost_per_unit=Decimal('1.2000'),
            current_stock=Decimal('500.0000'),
        )
        self.assertEqual(
            self._post_recipe(self.opt_size, self.milk_ing).status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            self._post_recipe(self.opt_milk, other).status_code,
            status.HTTP_201_CREATED,
        )
