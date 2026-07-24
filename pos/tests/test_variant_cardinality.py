"""FEATURE-010 — multi-select cardinality (min/max).

A multi-select variant group may declare min_selections and/or max_selections.
Both are nullable (null = unconstrained) and are only enforced when the group's
selection_type is 'multi'. An impossible window (min > max) is rejected at the
model clean() level.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, Item, Shift, User,
    VariantGroup, VariantOption, ProductVariantGroup,
    PosTransaction,
)


class MultiSelectCardinalityTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Pos',
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
        self.client.force_authenticate(self.cashier)

        self.item = Item.objects.create(
            name='Build-a-Bowl', price=Decimal('100.00'), stock=1000
        )
        # A MULTI group with min=1, max=2 attached to the item.
        self.toppings = VariantGroup.objects.create(
            name='Toppings', selection_type='multi',
            min_selections=1, max_selections=2,
        )
        self.opt_a = VariantOption.objects.create(
            group=self.toppings, name='Cheese', price_modifier=Decimal('10.00')
        )
        self.opt_b = VariantOption.objects.create(
            group=self.toppings, name='Bacon', price_modifier=Decimal('15.00')
        )
        self.opt_c = VariantOption.objects.create(
            group=self.toppings, name='Egg', price_modifier=Decimal('12.00')
        )
        ProductVariantGroup.objects.create(
            product=self.item, group=self.toppings, enabled=True
        )

    def _sel(self, *option_ids):
        return [
            {'group_id': str(self.toppings.id), 'option_id': str(oid)}
            for oid in option_ids
        ]

    def _post(self, selections):
        return self.client.post(
            '/api/pos/transactions/',
            {'items': [{
                'item_id': str(self.item.id), 'quantity': 1,
                'variant_selections': selections,
            }], 'payment_method': 'cash', 'cash_received': '500.00'},
            format='json',
        )

    def test_below_min_is_rejected(self):
        resp = self._post(self._sel())  # zero selections, min is 1
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(PosTransaction.objects.count(), 0)

    def test_above_max_is_rejected(self):
        resp = self._post(
            self._sel(self.opt_a.id, self.opt_b.id, self.opt_c.id)
        )  # three selections, max is 2
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(PosTransaction.objects.count(), 0)

    def test_within_window_succeeds(self):
        resp = self._post(self._sel(self.opt_a.id, self.opt_b.id))
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Expected success, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(PosTransaction.objects.count(), 1)

    def test_min_max_ignored_on_single_group(self):
        # A single-select group carrying min/max metadata must NOT enforce it
        # (single is already capped at exactly one). Set an absurd min that
        # could never be met, then prove a normal single selection still rings.
        single = VariantGroup.objects.create(
            name='Size', selection_type='single',
            min_selections=5, max_selections=9,
        )
        opt = VariantOption.objects.create(
            group=single, name='Large', price_modifier=Decimal('0.00')
        )
        item = Item.objects.create(
            name='Latte', price=Decimal('100.00'), stock=100
        )
        ProductVariantGroup.objects.create(
            product=item, group=single, enabled=True
        )
        resp = self.client.post(
            '/api/pos/transactions/',
            {'items': [{
                'item_id': str(item.id), 'quantity': 1,
                'variant_selections': [
                    {'group_id': str(single.id), 'option_id': str(opt.id)},
                ],
            }], 'payment_method': 'cash', 'cash_received': '500.00'},
            format='json',
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"single group must ignore min/max, got {resp.status_code}: {resp.data}",
        )

    def test_min_greater_than_max_rejected_at_clean(self):
        bad = VariantGroup(
            name='Bad', selection_type='multi',
            min_selections=3, max_selections=1,
        )
        with self.assertRaises(ValidationError):
            bad.full_clean()
