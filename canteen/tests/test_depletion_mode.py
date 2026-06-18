"""BUG-003 — line-level depletion_mode (replace | add).

A variant recipe line can either REPLACE the item-level line for the same
ingredient (substitution, e.g. Oat milk replaces Regular milk) or ADD to it
(additive add-ons/sizes, e.g. Extra Shot / Large adds to the base shot). The
previous logic always replaced, so additive add-ons silently under-depleted the
base. These tests pin both modes at sale time and confirm the void path restores
symmetrically.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User,
    VariantGroup, VariantOption, ProductVariantGroup,
    IngredientUnit, Ingredient, RecipeIngredient,
)
from canteen.services import create_pos_transaction, _restore_ingredients


class DepletionModeTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP', vat_enabled=False,
            track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        self.item = Item.objects.create(
            name='Latte', price=Decimal('100.00'), stock=1000
        )

        self.group_shot = VariantGroup.objects.create(
            name='Shot', selection_type='single', is_required=False
        )
        self.opt_extra = VariantOption.objects.create(
            group=self.group_shot, name='Extra Shot', price_modifier=Decimal('0.00')
        )
        ProductVariantGroup.objects.create(
            product=self.item, group=self.group_shot, enabled=True
        )
        # A second, multi-select group for the multi add-on case.
        self.group_addon = VariantGroup.objects.create(
            name='Add-ons', selection_type='multiple', is_required=False
        )
        self.opt_addon = VariantOption.objects.create(
            group=self.group_addon, name='Double Up', price_modifier=Decimal('0.00')
        )
        ProductVariantGroup.objects.create(
            product=self.item, group=self.group_addon, enabled=True
        )

        unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )
        self.START = Decimal('1000.0000')
        self.espresso = Ingredient.objects.create(
            name='Espresso', unit=unit, cost_per_unit=Decimal('1.0000'),
            current_stock=self.START,
        )
        # Item-level base recipe: 18g of espresso per Latte.
        self.base_line = RecipeIngredient.objects.create(
            item=self.item, ingredient=self.espresso,
            quantity_used=Decimal('18.0000'),
        )

    def _sell_with(self, option_ids):
        return create_pos_transaction(
            [{
                'item_id': self.item.id,
                'quantity': 1,
                'variant_selections': [
                    {'group_id': VariantOption.objects.get(id=oid).group_id,
                     'option_id': oid}
                    for oid in option_ids
                ],
            }],
            'cash', cashier=self.cashier, cash_received=Decimal('200.00'),
        )

    # (a) replace: variant 20g suppresses the 18g base → 20g depleted.
    def test_replace_mode_suppresses_base(self):
        RecipeIngredient.objects.create(
            item=self.item, variant=self.opt_extra, ingredient=self.espresso,
            quantity_used=Decimal('20.0000'), depletion_mode='replace',
        )
        self._sell_with([self.opt_extra.id])
        self.espresso.refresh_from_db()
        self.assertEqual(self.espresso.current_stock, self.START - Decimal('20.0000'))

    # (b) add: variant 20g adds to the 18g base → 38g depleted.
    def test_add_mode_sums_with_base(self):
        RecipeIngredient.objects.create(
            item=self.item, variant=self.opt_extra, ingredient=self.espresso,
            quantity_used=Decimal('20.0000'), depletion_mode='add',
        )
        self._sell_with([self.opt_extra.id])
        self.espresso.refresh_from_db()
        self.assertEqual(self.espresso.current_stock, self.START - Decimal('38.0000'))

    # (c) multi-select: base + two 'add' add-ons all sum (18 + 20 + 5 = 43).
    def test_multi_select_add_addons_all_sum(self):
        RecipeIngredient.objects.create(
            item=self.item, variant=self.opt_extra, ingredient=self.espresso,
            quantity_used=Decimal('20.0000'), depletion_mode='add',
        )
        RecipeIngredient.objects.create(
            item=self.item, variant=self.opt_addon, ingredient=self.espresso,
            quantity_used=Decimal('5.0000'), depletion_mode='add',
        )
        self._sell_with([self.opt_extra.id, self.opt_addon.id])
        self.espresso.refresh_from_db()
        self.assertEqual(self.espresso.current_stock, self.START - Decimal('43.0000'))

    # (d) void of an 'add'-mode sale restores the full summed amount.
    def test_void_of_add_mode_restores_full_sum(self):
        RecipeIngredient.objects.create(
            item=self.item, variant=self.opt_extra, ingredient=self.espresso,
            quantity_used=Decimal('20.0000'), depletion_mode='add',
        )
        txn = self._sell_with([self.opt_extra.id])
        self.espresso.refresh_from_db()
        self.assertEqual(self.espresso.current_stock, self.START - Decimal('38.0000'))

        for item_entry in txn.items.all():
            _restore_ingredients(item_entry.item, item_entry, item_entry.quantity)

        self.espresso.refresh_from_db()
        self.assertEqual(
            self.espresso.current_stock, self.START,
            'Void must restore both the base and the add-on amount (38g).',
        )

    # (e) default mode is 'replace' so pre-existing rows keep substitution.
    def test_default_mode_is_replace(self):
        line = RecipeIngredient.objects.create(
            item=self.item, variant=self.opt_extra, ingredient=self.espresso,
            quantity_used=Decimal('20.0000'),
        )
        self.assertEqual(line.depletion_mode, 'replace')
        self._sell_with([self.opt_extra.id])
        self.espresso.refresh_from_db()
        self.assertEqual(self.espresso.current_stock, self.START - Decimal('20.0000'))
