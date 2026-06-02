"""FEATURE-013 — multi-day / period report endpoint.

Aggregates across ZReports in a date range (manager/admin only). ZReports are
built seed-free at finalize time, so period totals exclude is_seed rows.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User, PosTransaction,
)
from canteen.services import create_pos_transaction, close_shift_and_finalize_z


class PeriodReportTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.item = Item.objects.create(
            name='Brewed Coffee', price=Decimal('50.00'), stock=1000
        )

    def _open_shift(self):
        return Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )

    def _sell(self, qty):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': qty}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )

    def _url(self, frm, to):
        return f'/api/canteen/reports/period/?from={frm}&to={to}'

    def test_period_aggregates_across_zreports(self):
        # Shift 1: two txns, gross 100.
        s1 = self._open_shift()
        self._sell(1)
        self._sell(1)
        close_shift_and_finalize_z(s1.id, Decimal('0.00'), self.cashier)
        # Shift 2: one txn qty 2, gross 100.
        s2 = self._open_shift()
        self._sell(2)
        close_shift_and_finalize_z(s2.id, Decimal('0.00'), self.cashier)

        from django.utils import timezone
        today = timezone.localdate().strftime('%Y-%m-%d')
        self.client.force_authenticate(self.manager)
        resp = self.client.get(self._url(today, today))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        summary = resp.data['summary']
        self.assertEqual(Decimal(summary['gross_total']), Decimal('200.00'))
        self.assertEqual(Decimal(summary['net_total']), Decimal('200.00'))
        self.assertEqual(Decimal(summary['cash_total']), Decimal('200.00'))
        self.assertEqual(summary['transaction_count'], 3)
        self.assertEqual(len(resp.data['daily']), 2)

    def test_is_seed_excluded_from_period_totals(self):
        s1 = self._open_shift()
        self._sell(1)                       # real: gross 50
        seed = self._sell(3)                # seed: gross 150 — must not count
        PosTransaction.objects.filter(pk=seed.pk).update(is_seed=True)
        close_shift_and_finalize_z(s1.id, Decimal('0.00'), self.cashier)

        from django.utils import timezone
        today = timezone.localdate().strftime('%Y-%m-%d')
        self.client.force_authenticate(self.manager)
        resp = self.client.get(self._url(today, today))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(resp.data['summary']['gross_total']), Decimal('50.00'))
        self.assertEqual(resp.data['summary']['transaction_count'], 1)

    def test_from_after_to_returns_400(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.get(self._url('2026-06-10', '2026-06-01'))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_range_returns_empty_not_404(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.get(self._url('2020-01-01', '2020-01-31'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['daily'], [])
        self.assertEqual(Decimal(resp.data['summary']['gross_total']), Decimal('0.00'))
        self.assertEqual(resp.data['summary']['transaction_count'], 0)

    def test_cashier_forbidden(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.get(self._url('2026-01-01', '2026-12-31'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
