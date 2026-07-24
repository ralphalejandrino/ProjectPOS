"""BUG-013 — per-item variant recipes stay isolated across items.

VariantGroup/VariantOption are SHARED across products (CategoryVariantGroup /
ProductVariantGroup). Before this fix a variant-scoped RecipeIngredient was
keyed only on the shared VariantOption (item forced NULL by BUG-001's XOR), so a
recipe authored "for one item's variant" attached to the shared option and bled
onto every item that used that option — the manager's exact report: setting
"Four Season"'s variant ingredient also surfaced on "Apple Green".

The fix gives every recipe line an owning item; a variant line is identified by
(item, variant). These tests reproduce the manager's two-item scenario over the
real API and assert: (1) saving the second item's variant recipe does not mutate
the first, (2) each item lists only its own variant line, and (3) sale depletion
and void restore stay scoped to the selling item (services.py symmetry).
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, Item, ItemCategory, Shift, User,
    VariantGroup, VariantOption, CategoryVariantGroup,
    IngredientUnit, Ingredient, RecipeIngredient,
)
from pos.services import create_pos_transaction, _restore_ingredients


class RecipeVariantIsolationTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP', vat_enabled=False,
            track_inventory=True, printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )

        # Two distinct items in one category...
        self.cat = ItemCategory.objects.create(name='Fruit Teas')
        self.apple = Item.objects.create(
            name='Fruit Tea - Apple Green', price=Decimal('100.00'),
            stock=1000, category=self.cat,
        )
        self.four = Item.objects.create(
            name='Fruit Tea - Four Season', price=Decimal('100.00'),
            stock=1000, category=self.cat,
        )

        # ...that SHARE a variant group + option via the category assignment.
        # This shared VariantOption is what bled across items before the fix.
        self.group = VariantGroup.objects.create(name='Sweetness', sort_order=0)
        self.regular = VariantOption.objects.create(group=self.group, name='Regular')
        CategoryVariantGroup.objects.create(category=self.cat, group=self.group)

        unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Milliliters'}
        )
        self.START = Decimal('1000.0000')
        self.apple_syrup = Ingredient.objects.create(
            name='Apple Green Tea Syrup', unit=unit,
            cost_per_unit=Decimal('0.10'), current_stock=self.START,
        )
        self.four_syrup = Ingredient.objects.create(
            name='Four Season Tea Syrup', unit=unit,
            cost_per_unit=Decimal('0.10'), current_stock=self.START,
        )
        self.manager_client_auth()

    def manager_client_auth(self):
        self.client.force_authenticate(self.manager)

    def _save_variant_recipe(self, item, ingredient, qty):
        """POST a variant-scoped recipe line the way the recipe builder does."""
        return self.client.post(
            '/api/pos/recipe-ingredients/',
            {
                'item': str(item.id),
                'variant': str(self.regular.id),
                'ingredient': str(ingredient.id),
                'quantity_used': str(qty),
                'depletion_mode': 'add',
            },
            format='json',
        )

    def _list_variant_recipe(self, item):
        resp = self.client.get(
            f'/api/pos/recipe-ingredients/'
            f'?item={item.id}&variant={self.regular.id}'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        rows = resp.data['results'] if isinstance(resp.data, dict) else resp.data
        return rows

    def test_second_save_does_not_bleed_onto_first_item(self):
        # Manager's exact sequence: author Apple Green first, then Four Season.
        r1 = self._save_variant_recipe(self.apple, self.apple_syrup, '5.0')
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED, r1.data)

        r2 = self._save_variant_recipe(self.four, self.four_syrup, '7.0')
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED, r2.data)

        # Each item retains ONLY its own variant line — no overwrite, no dup.
        apple_rows = self._list_variant_recipe(self.apple)
        four_rows = self._list_variant_recipe(self.four)

        self.assertEqual(len(apple_rows), 1, apple_rows)
        self.assertEqual(len(four_rows), 1, four_rows)
        self.assertEqual(apple_rows[0]['ingredient'], self.apple_syrup.id)
        self.assertEqual(four_rows[0]['ingredient'], self.four_syrup.id)
        # The first item's line was untouched by the second save.
        self.assertEqual(apple_rows[0]['quantity_used'], '5.0000')

    def test_depletion_and_void_are_scoped_to_the_selling_item(self):
        self._save_variant_recipe(self.apple, self.apple_syrup, '5.0')
        self._save_variant_recipe(self.four, self.four_syrup, '7.0')

        # Sell Apple Green with the shared "Regular" option selected.
        txn = create_pos_transaction(
            [{
                'item_id': self.apple.id,
                'quantity': 1,
                'variant_selections': [
                    {'group_id': self.group.id, 'option_id': self.regular.id},
                ],
            }],
            'cash', cashier=self.cashier, cash_received=Decimal('200.00'),
        )

        self.apple_syrup.refresh_from_db()
        self.four_syrup.refresh_from_db()
        # Only Apple Green's syrup depletes — Four Season's is untouched even
        # though it shares the same VariantOption.
        self.assertEqual(self.apple_syrup.current_stock, self.START - Decimal('5.0000'))
        self.assertEqual(self.four_syrup.current_stock, self.START)

        # Void restores symmetrically and only the selling item's ingredient.
        for entry in txn.items.all():
            _restore_ingredients(entry.item, entry, entry.quantity)
        self.apple_syrup.refresh_from_db()
        self.four_syrup.refresh_from_db()
        self.assertEqual(self.apple_syrup.current_stock, self.START)
        self.assertEqual(self.four_syrup.current_stock, self.START)
