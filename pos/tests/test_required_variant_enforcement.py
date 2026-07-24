"""ISSUE-073 — required-variant enforcement cannot be bypassed.

The required-group check used to live inside `if variant_selections:`, so a
payload that simply omitted the variant_selections key skipped all variant
validation and saved a transaction missing a required selection. These tests
lock the door: an item with a required variant group must reject a sale that
omits (or empties) the selection.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, Item, Shift, User,
    VariantGroup, VariantOption, ProductVariantGroup,
    PosTransaction,
)


class RequiredVariantEnforcementTests(APITestCase):
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
            name='Milk Tea', price=Decimal('100.00'), stock=1000
        )
        # A REQUIRED variant group attached to the item.
        self.group_size = VariantGroup.objects.create(
            name='Size', selection_type='single', is_required=True
        )
        self.opt_large = VariantOption.objects.create(
            group=self.group_size, name='Large', price_modifier=Decimal('20.00')
        )
        ProductVariantGroup.objects.create(
            product=self.item, group=self.group_size, enabled=True
        )

    def _post(self, items):
        return self.client.post(
            '/api/pos/transactions/',
            {'items': items, 'payment_method': 'cash',
             'cash_received': '500.00'},
            format='json',
        )

    def test_omitting_variant_selections_key_is_rejected(self):
        # No variant_selections key at all — the bypass path.
        resp = self._post([{'item_id': str(self.item.id), 'quantity': 1}])
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(PosTransaction.objects.count(), 0)

    def test_empty_variant_selections_is_rejected(self):
        resp = self._post([{
            'item_id': str(self.item.id), 'quantity': 1,
            'variant_selections': [],
        }])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PosTransaction.objects.count(), 0)

    def test_required_selection_present_succeeds(self):
        # Positive control: providing the required selection rings up fine.
        resp = self._post([{
            'item_id': str(self.item.id), 'quantity': 1,
            'variant_selections': [
                {'group_id': str(self.group_size.id),
                 'option_id': str(self.opt_large.id)},
            ],
        }])
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Expected success, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(PosTransaction.objects.count(), 1)
