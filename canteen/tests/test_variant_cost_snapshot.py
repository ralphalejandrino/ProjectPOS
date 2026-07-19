"""C1 — cost-at-sale must include the SELECTED variants' recipe lines
(depletion parity). Regression for the live PROD finding (2026-07-19): 28 of 50
active items encode their whole recipe per size variant (no base lines), so
item_recipe_cost returned None and every sale snapshotted purchase_price(0) —
the report could never show a margin for most of the menu. Negative controls
pin resale fallback, base-only behavior, unselected variants, and replace-mode
substitution."""

from decimal import Decimal

from django.test import TestCase

from canteen.models import (
    BusinessProfile, CategoryVariantGroup, Ingredient, IngredientUnit, Item,
    ItemCategory, PosTransactionItem, RecipeIngredient, Shift, User,
    VariantGroup, VariantOption,
)
from canteen.services import create_pos_transaction, item_effective_unit_cost

D = Decimal


class VariantCostSnapshotTests(TestCase):
    def setUp(self):
        BusinessProfile.objects.create(
            business_name='PROD', currency='PHP', vat_enabled=False,
            track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(username='cashier', password='x', role='cashier')
        Shift.objects.create(cashier=self.cashier, opening_cash=D('0'), is_open=True)
        ml = IngredientUnit.objects.get_or_create(abbreviation='ml', defaults={'name': 'ml'})[0]
        pcs = IngredientUnit.objects.get_or_create(abbreviation='pcs', defaults={'name': 'pcs'})[0]

        self.cat = ItemCategory.objects.create(name='Drinks')
        self.size = VariantGroup.objects.create(name='Size', sort_order=0)
        self.v12 = VariantOption.objects.create(group=self.size, name='12oz')
        self.v16 = VariantOption.objects.create(group=self.size, name='16oz')
        CategoryVariantGroup.objects.create(category=self.cat, group=self.size)

        self.cup12 = Ingredient.objects.create(
            name='Cup 12oz', unit=pcs, cost_per_unit=D('4.20'), current_stock=D('100'))
        self.cup16 = Ingredient.objects.create(
            name='Cup 16oz', unit=pcs, cost_per_unit=D('4.40'), current_stock=D('100'))
        self.milk = Ingredient.objects.create(
            name='Milk', unit=ml, cost_per_unit=D('0.0766'), current_stock=D('5000'))
        self.syrup = Ingredient.objects.create(
            name='Syrup', unit=ml, cost_per_unit=D('0.3646'), current_stock=D('1000'))

    def _item(self, name, price='59.00'):
        return Item.objects.create(
            name=name, price=D(price), stock=0, category=self.cat)

    def _sell(self, item, options=None, qty=1):
        txn = create_pos_transaction(
            [{
                'item_id': item.id, 'quantity': qty,
                'variant_selections': [
                    {'group_id': self.size.id, 'option_id': o.id}
                    for o in (options or [])
                ],
            }],
            'cash', cashier=self.cashier, cash_received=D('500.00'),
        )
        return PosTransactionItem.objects.get(pos_transaction=txn)

    def _variant_only_item(self):
        """The PROD menu shape: whole recipe per size, no base lines."""
        item = self._item('Iced Coffee - Spanish Latte')
        for v, cup, milk_q, syr_q in (
            (self.v12, self.cup12, '100', '15'),
            (self.v16, self.cup16, '120', '20'),
        ):
            RecipeIngredient.objects.create(item=item, variant=v, ingredient=cup,
                                            quantity_used=D('1'), depletion_mode='add')
            RecipeIngredient.objects.create(item=item, variant=v, ingredient=self.milk,
                                            quantity_used=D(milk_q), depletion_mode='add')
            RecipeIngredient.objects.create(item=item, variant=v, ingredient=self.syrup,
                                            quantity_used=D(syr_q), depletion_mode='add')
        return item

    def test_variant_only_recipe_snapshots_selected_size_cost(self):
        item = self._variant_only_item()
        # 12oz: 1*4.20 + 100*0.0766 + 15*0.3646 = 4.20 + 7.66 + 5.469 = 17.329
        line = self._sell(item, [self.v12])
        self.assertEqual(line.unit_cost, D('17.3290'))
        # 16oz: 1*4.40 + 120*0.0766 + 20*0.3646 = 4.40 + 9.192 + 7.292 = 20.884
        line16 = self._sell(item, [self.v16])
        self.assertEqual(line16.unit_cost, D('20.8840'))

    def test_variant_only_recipe_was_zero_before_fix(self):
        # Documents the exact pre-fix failure: base-only derivation sees no
        # lines -> None -> purchase_price fallback (None here).
        item = self._variant_only_item()
        from canteen.services import item_recipe_cost
        self.assertIsNone(item_recipe_cost(item))
        self.assertIsNotNone(item_effective_unit_cost(item, [self.v12.id]))

    def test_add_mode_stacks_on_base_recipe(self):
        item = self._item('Latte')
        RecipeIngredient.objects.create(item=item, ingredient=self.milk,
                                        quantity_used=D('100'))  # base 7.66
        RecipeIngredient.objects.create(item=item, variant=self.v16, ingredient=self.syrup,
                                        quantity_used=D('10'), depletion_mode='add')  # +3.646
        line = self._sell(item, [self.v16])
        self.assertEqual(line.unit_cost, D('11.3060'))

    def test_replace_mode_substitutes_base_line(self):
        item = self._item('Latte-R')
        RecipeIngredient.objects.create(item=item, ingredient=self.milk,
                                        quantity_used=D('100'))  # replaced away
        RecipeIngredient.objects.create(item=item, variant=self.v16, ingredient=self.milk,
                                        quantity_used=D('120'), depletion_mode='replace')
        line = self._sell(item, [self.v16])
        self.assertEqual(line.unit_cost, (D('120') * D('0.0766')).quantize(D('0.0001')))

    def test_unselected_variant_lines_cost_nothing(self):
        item = self._item('Latte-U')
        RecipeIngredient.objects.create(item=item, ingredient=self.milk,
                                        quantity_used=D('100'))
        RecipeIngredient.objects.create(item=item, variant=self.v16, ingredient=self.syrup,
                                        quantity_used=D('10'), depletion_mode='add')
        line = self._sell(item)  # no size picked
        self.assertEqual(line.unit_cost, D('7.6600'))

    def test_base_only_recipe_matches_old_behavior(self):
        item = self._item('Brewed')
        RecipeIngredient.objects.create(item=item, ingredient=self.milk,
                                        quantity_used=D('100'))
        from canteen.services import item_recipe_cost
        self.assertEqual(item_effective_unit_cost(item, []), item_recipe_cost(item))

    def test_resale_item_still_falls_back_to_purchase_price(self):
        item = Item.objects.create(name='Bottled Water', price=D('20'), stock=50,
                                   purchase_price=D('12.00'), category=self.cat)
        line = self._sell(item)
        self.assertEqual(line.unit_cost, D('12.00'))

    def test_zero_quantity_variant_line_skipped(self):
        item = self._item('Latte-Z')
        RecipeIngredient.objects.create(item=item, variant=self.v16, ingredient=self.milk,
                                        quantity_used=D('0'), depletion_mode='add')
        RecipeIngredient.objects.create(item=item, variant=self.v16, ingredient=self.syrup,
                                        quantity_used=D('10'), depletion_mode='add')
        self.assertEqual(item_effective_unit_cost(item, [self.v16.id]), D('3.6460'))

    def test_cost_matches_what_depletion_consumes(self):
        """The invariant behind the whole fix: snapshot cost == sum over the
        depleted ledger moves of qty * ingredient cost."""
        item = self._variant_only_item()
        stocks_before = {i.pk: i.current_stock for i in Ingredient.objects.all()}
        line = self._sell(item, [self.v12])
        consumed_cost = D('0')
        for ing in Ingredient.objects.all():
            used = stocks_before[ing.pk] - ing.current_stock
            consumed_cost += used * ing.cost_per_unit
        self.assertEqual(line.unit_cost, consumed_cost.quantize(D('0.0001')))
