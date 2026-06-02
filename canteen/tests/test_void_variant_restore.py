"""ISSUE-072 — void ingredient-restore must match by (group, option) pair.

Regression test: when two variant groups each define an option with the
same name (e.g. both have "Regular"), voiding a sale that selected only
one of them must restore only that group's ingredient — not both. The old
code matched VariantOption by option_name alone, which restored every
group's "Regular" recipe.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User,
    VariantGroup, VariantOption, ProductVariantGroup,
    IngredientUnit, Ingredient, RecipeIngredient,
    PosTransaction,
)
from canteen.services import create_pos_transaction, _restore_ingredients


class VoidVariantRestoreTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen',
            currency='PHP',
            vat_enabled=False,
            track_inventory=True,
            printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        self.item = Item.objects.create(
            name='Iced Coffee', price=Decimal('100.00'), stock=1000
        )

        # Two distinct groups, each with an option literally named "Regular".
        self.group_size = VariantGroup.objects.create(
            name='Size', selection_type='single', is_required=False
        )
        self.group_sugar = VariantGroup.objects.create(
            name='Sugar', selection_type='single', is_required=False
        )
        self.opt_size_regular = VariantOption.objects.create(
            group=self.group_size, name='Regular', price_modifier=Decimal('0.00')
        )
        self.opt_sugar_regular = VariantOption.objects.create(
            group=self.group_sugar, name='Regular', price_modifier=Decimal('0.00')
        )

        # Both groups are effective for this item.
        ProductVariantGroup.objects.create(
            product=self.item, group=self.group_size, enabled=True
        )
        ProductVariantGroup.objects.create(
            product=self.item, group=self.group_sugar, enabled=True
        )

        unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )
        self.START = Decimal('100.0000')
        self.ing_size = Ingredient.objects.create(
            name='Beans', unit=unit, cost_per_unit=Decimal('1.0000'),
            current_stock=self.START,
        )
        self.ing_sugar = Ingredient.objects.create(
            name='Syrup', unit=unit, cost_per_unit=Decimal('1.0000'),
            current_stock=self.START,
        )

        # Variant-level recipes: each "Regular" depletes a different ingredient.
        RecipeIngredient.objects.create(
            variant=self.opt_size_regular, ingredient=self.ing_size,
            quantity_used=Decimal('2.0000'),
        )
        RecipeIngredient.objects.create(
            variant=self.opt_sugar_regular, ingredient=self.ing_sugar,
            quantity_used=Decimal('3.0000'),
        )

    def test_void_restores_only_selected_group_ingredient(self):
        # Sell selecting ONLY Size → Regular (Sugar group not selected).
        txn = create_pos_transaction(
            [{
                'item_id': self.item.id,
                'quantity': 1,
                'variant_selections': [
                    {'group_id': self.group_size.id,
                     'option_id': self.opt_size_regular.id},
                ],
            }],
            'cash',
            cashier=self.cashier,
            cash_received=Decimal('200.00'),
        )

        self.ing_size.refresh_from_db()
        self.ing_sugar.refresh_from_db()
        # Only Size/Regular's ingredient was depleted at sale time.
        self.assertEqual(self.ing_size.current_stock, self.START - Decimal('2.0000'))
        self.assertEqual(self.ing_sugar.current_stock, self.START)

        # Void: restore stock for each item line.
        for item_entry in txn.items.all():
            _restore_ingredients(item_entry.item, item_entry, item_entry.quantity)

        self.ing_size.refresh_from_db()
        self.ing_sugar.refresh_from_db()
        # Size restored back to start; Sugar untouched (NOT over-restored).
        self.assertEqual(self.ing_size.current_stock, self.START)
        self.assertEqual(
            self.ing_sugar.current_stock, self.START,
            "Sugar group's ingredient must not be restored — it was never "
            "depleted; matching by option_name alone would wrongly bump it.",
        )
