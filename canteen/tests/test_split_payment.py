"""FEATURE-016 — split / multi-payment transactions.

A transaction may be paid across more than one method (e.g. ₱30 cash + ₱70
GCash). Tender is recorded as PaymentLine rows whose amounts sum to the charged
total; PosTransaction.payment_method becomes the "primary" method (the largest
line). The X/Z payment breakdown and cash reconciliation aggregate over
PaymentLine rows. Existing transactions are backfilled with one line.
"""

from decimal import Decimal

from django.apps import apps as global_apps
from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User, PaymentLine, PosTransaction,
)
from canteen.services import create_pos_transaction, close_shift_and_finalize_z


class SplitPaymentTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('100.00'), is_open=True
        )
        self.item = Item.objects.create(
            name='Combo', price=Decimal('100.00'), stock=1000
        )
        self.client.force_authenticate(self.cashier)

    def _post(self, payment_lines):
        return self.client.post(
            '/api/canteen/transactions/',
            {'items': [{'item_id': str(self.item.id), 'quantity': 1}],
             'payment_method': 'cash', 'payment_lines': payment_lines},
            format='json',
        )

    def test_single_payment_creates_one_line(self):
        txn = create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}],
            'cash', cashier=self.cashier, cash_received=Decimal('200.00'),
        )
        lines = txn.payment_lines.all()
        self.assertEqual(lines.count(), 1)
        self.assertEqual(lines[0].method, 'cash')
        self.assertEqual(lines[0].amount, txn.net_total)

    def test_split_must_sum_to_total(self):
        # 40 + 40 = 80, but the sale is 100 → rejected.
        resp = self._post([
            {'method': 'cash', 'amount': '40.00'},
            {'method': 'gcash', 'amount': '40.00'},
        ])
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Expected 400, got {resp.status_code}: {resp.data}",
        )
        self.assertEqual(PosTransaction.objects.count(), 0)

    def test_split_sum_matches_succeeds(self):
        resp = self._post([
            {'method': 'cash', 'amount': '30.00'},
            {'method': 'gcash', 'amount': '70.00'},
        ])
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Expected success, got {resp.status_code}: {resp.data}",
        )
        txn = PosTransaction.objects.get()
        self.assertEqual(txn.payment_lines.count(), 2)
        # Primary method is the largest line.
        self.assertEqual(txn.payment_method, 'gcash')

    def test_negative_line_rejected(self):
        resp = self._post([
            {'method': 'cash', 'amount': '-30.00'},
            {'method': 'gcash', 'amount': '130.00'},
        ])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PosTransaction.objects.count(), 0)

    def test_split_zreport_cash_reflects_cash_lines_only(self):
        self._post([
            {'method': 'cash', 'amount': '30.00'},
            {'method': 'gcash', 'amount': '70.00'},
        ])
        z = close_shift_and_finalize_z(
            self.shift.id, cash_counted=None, cashier_user=self.cashier
        )
        self.assertEqual(z.payment_breakdown['cash'], '30.00')
        self.assertEqual(z.payment_breakdown['gcash'], '70.00')
        self.assertEqual(z.cash_collected, Decimal('30.00'))
        # opening 100 + 30 cash tender = 130.
        self.assertEqual(z.cash_expected, Decimal('130.00'))

    def test_backfill_creates_one_payment_line(self):
        from importlib import import_module
        mod = import_module('canteen.migrations.0039_paymentline')

        # Simulate a pre-existing transaction with no payment lines.
        txn = create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}],
            'gcash', cashier=self.cashier,
        )
        txn.payment_lines.all().delete()
        self.assertEqual(txn.payment_lines.count(), 0)

        mod.backfill_payment_lines(global_apps, None)

        lines = txn.payment_lines.all()
        self.assertEqual(lines.count(), 1)
        self.assertEqual(lines[0].method, 'gcash')
        self.assertEqual(lines[0].amount, txn.net_total)
