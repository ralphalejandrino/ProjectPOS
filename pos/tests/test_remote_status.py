"""FEATURE-026 — client remote ops view (admin-only live cafe status)."""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, Item, Shift, User, PosTransaction,
)
from pos.services import create_pos_transaction


class RemoteStatusTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Pos', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.admin = User.objects.create_user(
            username='owner', password='x', role='admin'
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.item = Item.objects.create(
            name='Brewed Coffee', price=Decimal('50.00'), stock=1000
        )

    def _sell(self, qty):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': qty}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )

    def test_remote_returns_shift_status_and_today_gross(self):
        shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        self._sell(2)                       # real: 100.00
        seed = self._sell(3)                # seed: 150.00 — excluded
        PosTransaction.objects.filter(pk=seed.pk).update(is_seed=True)

        self.client.force_authenticate(self.admin)
        resp = self.client.get('/api/pos/remote/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['shift_open'])
        self.assertEqual(resp.data['shift']['cashier'], 'cashier')
        self.assertEqual(resp.data['shift']['id'], shift.id)
        # today_gross is seed-free.
        self.assertEqual(Decimal(resp.data['today_gross']), Decimal('100.00'))
        # recent_transactions excludes the seed row and carries no PII.
        self.assertEqual(len(resp.data['recent_transactions']), 1)
        self.assertEqual(resp.data['recent_transactions'][0]['payment_method'], 'cash')

    def test_remote_no_open_shift(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get('/api/pos/remote/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['shift_open'])
        self.assertIsNone(resp.data['shift'])

    def test_manager_forbidden(self):
        manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.client.force_authenticate(manager)
        resp = self.client.get('/api/pos/remote/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
