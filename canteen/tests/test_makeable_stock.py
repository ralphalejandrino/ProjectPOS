"""FEATURE-046 — ingredient-derived "makeable" stock (read-time, zero-migration).

`makeable` = how many units of a recipe item can be produced from current
ingredient inventory: min over recipe lines of floor(current_stock /
quantity_used). It is a read-only overlay alongside the stored Item.stock
counter and NEVER gates a sale (the sale path stays on stored stock).

Covers the pure helper (compute_makeable), the serializer surface, and the
non-gating guarantee on the real sale path.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User,
    IngredientUnit, Ingredient, RecipeIngredient,
    VariantGroup, VariantOption, ProductVariantGroup,
)
from canteen.services import (
    create_pos_transaction, compute_makeable, item_makeable,
)


class _Line:
    """Minimal stand-in for a RecipeIngredient row, so the pure helper can be
    exercised with a None quantity_used — a state the NOT NULL DB column cannot
    persist, but which the helper must still defend against (no crash)."""
    def __init__(self, current_stock, quantity_used):
        self.ingredient = type('I', (), {'current_stock': current_stock})()
        self.quantity_used = quantity_used


class ComputeMakeableHelperTests(APITestCase):
    """Pure-function behaviour of compute_makeable()."""

    def test_no_lines_is_no_recipe(self):
        self.assertEqual(compute_makeable([]), (None, 'no_recipe'))

    def test_scarcest_ingredient_binds(self):
        lines = [
            _Line(Decimal('100.0000'), Decimal('2.0000')),  # 50 batches
            _Line(Decimal('30.0000'), Decimal('10.0000')),  # 3 batches  ← binds
            _Line(Decimal('40.0000'), Decimal('4.0000')),   # 10 batches
        ]
        self.assertEqual(compute_makeable(lines), (3, 'ok'))

    def test_floor_not_round(self):
        # 29 / 10 = 2.9 → 2 whole units, never 3.
        self.assertEqual(compute_makeable([_Line(Decimal('29'), Decimal('10'))]),
                         (2, 'ok'))

    def test_zero_quantity_used_flags_incomplete_no_zerodivision(self):
        lines = [
            _Line(Decimal('100'), Decimal('2')),   # valid
            _Line(Decimal('100'), Decimal('0')),   # 0 → incomplete, must not /0
        ]
        # Would raise ZeroDivisionError if the 0 line were divided or silently
        # skipped instead of flagging the whole item.
        self.assertEqual(compute_makeable(lines), (None, 'incomplete_recipe'))

    def test_null_quantity_used_flags_incomplete(self):
        lines = [_Line(Decimal('100'), None)]
        self.assertEqual(compute_makeable(lines), (None, 'incomplete_recipe'))

    def test_negative_ingredient_stock_clamps_to_zero(self):
        # ISSUE-069 lets ingredient stock go negative (oversold) — "can make 0",
        # never a negative count.
        lines = [_Line(Decimal('-5.0000'), Decimal('2.0000'))]
        self.assertEqual(compute_makeable(lines), (0, 'ok'))


class MakeableSerializerTests(APITestCase):
    """FEATURE-046 surface on the items endpoint (ItemSerializer)."""

    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )
        self.client.force_authenticate(self.manager)

    def _ingredient(self, name, stock):
        return Ingredient.objects.create(
            name=name, unit=self.unit, cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal(stock),
        )

    def _item(self, name, stock=100):
        return Item.objects.create(name=name, price=Decimal('50.00'), stock=stock)

    def _get_item(self, item_id):
        resp = self.client.get(f'/api/canteen/items/{item_id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data

    def test_recipe_item_reports_numeric_makeable(self):
        item = self._item('Latte')
        beans = self._ingredient('Beans', '50.0000')
        milk = self._ingredient('Milk', '12.0000')
        RecipeIngredient.objects.create(item=item, ingredient=beans,
                                        quantity_used=Decimal('5.0000'))   # 10
        RecipeIngredient.objects.create(item=item, ingredient=milk,
                                        quantity_used=Decimal('4.0000'))   # 3 ←
        data = self._get_item(item.id)
        self.assertEqual(data['makeable'], 3)
        self.assertEqual(data['makeable_status'], 'ok')

    def test_pure_good_is_no_recipe_with_null_makeable(self):
        item = self._item('Bottled Water')
        data = self._get_item(item.id)
        self.assertIsNone(data['makeable'])
        self.assertEqual(data['makeable_status'], 'no_recipe')

    def test_incomplete_recipe_zero_line(self):
        item = self._item('Broken Mocha')
        ing = self._ingredient('Syrup', '100.0000')
        RecipeIngredient.objects.create(item=item, ingredient=ing,
                                        quantity_used=Decimal('0.0000'))
        data = self._get_item(item.id)
        self.assertIsNone(data['makeable'])
        self.assertEqual(data['makeable_status'], 'incomplete_recipe')

    def test_variant_only_item_classifies_without_crash(self):
        """An item whose recipe lives on its variants (no direct item-FK lines)
        has no resolvable recipe at the no-variant surface; stored stock tracks
        it like a pure good → 'no_recipe'. Must not crash."""
        item = self._item('Variant Latte')
        group = VariantGroup.objects.create(name='Size')
        option = VariantOption.objects.create(group=group, name='Large')
        ProductVariantGroup.objects.create(product=item, group=group, enabled=True)
        ing = self._ingredient('Espresso', '100.0000')
        # BUG-013: a variant line is owned by the item but scoped to the option.
        RecipeIngredient.objects.create(item=item, variant=option, ingredient=ing,
                                        quantity_used=Decimal('5.0000'))
        # Sanity: the item has zero BASE (variant-null) recipe lines — the only
        # lines makeable computes over. Its variant line must not count.
        self.assertEqual(
            item.recipe_ingredients.filter(variant__isnull=True).count(), 0)
        data = self._get_item(item.id)
        self.assertIsNone(data['makeable'])
        self.assertEqual(data['makeable_status'], 'no_recipe')

    def test_makeable_is_read_only_on_round_trip_write(self):
        """PATCHing an item with makeable/makeable_status in the body must not
        persist them (no model field) and must not error."""
        item = self._item('Cappuccino')
        ing = self._ingredient('Beans', '20.0000')
        RecipeIngredient.objects.create(item=item, ingredient=ing,
                                        quantity_used=Decimal('5.0000'))   # 4
        resp = self.client.patch(
            f'/api/canteen/items/{item.id}/',
            {'makeable': 999, 'makeable_status': 'ok', 'low_stock_threshold': 7},
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_202_ACCEPTED))
        item.refresh_from_db()
        self.assertEqual(item.low_stock_threshold, 7)        # real field written
        self.assertFalse(hasattr(item, 'makeable'))          # not a model attr
        # Re-read: makeable still the computed value, not the injected 999.
        self.assertEqual(self._get_item(item.id)['makeable'], 4)

    def test_reports_contain_no_makeable_field(self):
        """The weekly report payload (and its inventory notices) must not leak
        the makeable field — it lives only on the items endpoint."""
        from django.utils import timezone
        resp = self.client.get(
            f"/api/canteen/reports/period/?week={timezone.localdate():%Y-%m-%d}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = str(resp.data)
        self.assertNotIn('makeable', body)


class MakeableNonGatingTests(APITestCase):
    """The critical guarantee: makeable is informational only — a sale gates on
    STORED Item.stock, never on makeable."""

    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        Shift.objects.create(cashier=self.cashier, opening_cash=Decimal('0.00'),
                             is_open=True)
        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )

    def test_sale_succeeds_when_makeable_zero_but_stored_stock_positive(self):
        # Stored stock 5 (sellable), but the ingredient is exhausted → makeable 0.
        item = Item.objects.create(name='Mocha', price=Decimal('50.00'), stock=5)
        ing = Ingredient.objects.create(
            name='Cocoa', unit=self.unit, cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal('0.0000'),
        )
        RecipeIngredient.objects.create(item=item, ingredient=ing,
                                        quantity_used=Decimal('2.0000'))
        self.assertEqual(item_makeable(item), (0, 'ok'))   # can make 0...

        txn = create_pos_transaction(
            [{'item_id': item.id, 'quantity': 1}], 'cash',
            cashier=self.cashier, cash_received=Decimal('100.00'),
        )
        self.assertIsNotNone(txn)            # ...yet the sale still goes through
        item.refresh_from_db()
        self.assertEqual(item.stock, 4)      # stored stock decremented as normal
