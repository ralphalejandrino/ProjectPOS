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


class WeeklyReportTests(APITestCase):
    """ISSUE-118 — Weekly Performance Report (?week= mode).

    Filter parity note: like the original FEATURE-013 period report, the
    weekly mode applies NO is_official filter — every finalized ZReport in
    the window counts; seed rows are excluded at Z finalize time (FLAG-047)
    and by the live/X queries directly.
    """

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
        self.client.force_authenticate(self.manager)

    def _open_shift(self):
        return Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )

    def _sell(self, qty):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': qty}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )

    def _url(self, week):
        return f'/api/canteen/reports/period/?week={week}'

    def test_week_window_is_monday_to_sunday(self):
        """Any date inside the week snaps to the same Mon–Sun PHT window."""
        import datetime as dt
        for anchor in ('2026-06-08', '2026-06-10', '2026-06-14'):
            resp = self.client.get(self._url(anchor))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp.data['week_start'], '2026-06-08')  # Monday
            self.assertEqual(resp.data['week_end'], '2026-06-14')    # Sunday
            self.assertEqual(len(resp.data['days']), 7)
            self.assertEqual(resp.data['days'][0]['day_name'], 'Mon')
            self.assertEqual(resp.data['days'][6]['day_name'], 'Sun')
        start = dt.date(2026, 6, 8)
        self.assertEqual(start.weekday(), 0)

    def test_week_combines_finalized_z_and_live_x(self):
        """Finalized Z (closed shift) + live X (open shift) both count for
        today, and the day is labelled live while keeping its Z count."""
        from django.utils import timezone
        # Shift 1 — closed: gross 100 frozen into a Z.
        s1 = self._open_shift()
        self._sell(1)
        self._sell(1)
        close_shift_and_finalize_z(s1.id, Decimal('0.00'), self.cashier)
        # Shift 2 — still open: gross 150 live.
        self._open_shift()
        self._sell(3)

        today = timezone.localdate()
        resp = self.client.get(self._url(today.strftime('%Y-%m-%d')))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        summary = resp.data['summary']
        self.assertEqual(Decimal(summary['gross_total']), Decimal('250.00'))
        self.assertEqual(summary['transaction_count'], 3)
        self.assertEqual(Decimal(summary['cash_total']), Decimal('250.00'))
        self.assertEqual(resp.data['live_date'], today.strftime('%Y-%m-%d'))

        day = next(d for d in resp.data['days']
                   if d['date'] == today.strftime('%Y-%m-%d'))
        self.assertEqual(day['status'], 'live')
        self.assertEqual(day['finalized_shifts'], 1)
        self.assertEqual(Decimal(day['gross']), Decimal('250.00'))
        self.assertEqual(day['transaction_count'], 3)

        # Top items cover both sources.
        self.assertEqual(resp.data['top_items'][0]['name'], 'Brewed Coffee')
        self.assertEqual(resp.data['top_items'][0]['quantity'], 5)

    def test_zero_transaction_week_renders_without_error(self):
        """A week with no Z's and no live data returns zeros, all 7 days."""
        resp = self.client.get(self._url('2020-03-04'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(resp.data['summary']['gross_total']),
                         Decimal('0.00'))
        self.assertEqual(resp.data['summary']['transaction_count'], 0)
        self.assertEqual(len(resp.data['days']), 7)
        self.assertEqual(resp.data['top_items'], [])
        self.assertIsNone(resp.data['previous_week'])
        self.assertIsNone(resp.data['live_date'])

    def test_week_over_week_delta(self):
        """Delta appears when the prior week holds a Z, omitted otherwise."""
        from datetime import timedelta
        from django.utils import timezone
        from canteen.models import ZReport

        s1 = self._open_shift()
        self._sell(2)   # gross 100
        close_shift_and_finalize_z(s1.id, Decimal('0.00'), self.cashier)
        today = timezone.localdate()
        resp = self.client.get(self._url(today.strftime('%Y-%m-%d')))
        # No prior-week data yet → delta gracefully omitted.
        self.assertIsNone(resp.data['previous_week'])

        # Plant the Z one week back (queryset update bypasses the immutable
        # save() guard — test-only relocation).
        ZReport.objects.all().update(business_date=today - timedelta(days=7))
        s2 = self._open_shift()
        self._sell(3)   # gross 150 this week (live)
        close_shift_and_finalize_z(s2.id, Decimal('0.00'), self.cashier)

        resp = self.client.get(self._url(today.strftime('%Y-%m-%d')))
        prev = resp.data['previous_week']
        self.assertIsNotNone(prev)
        self.assertEqual(Decimal(prev['gross_total']), Decimal('100.00'))
        self.assertEqual(Decimal(prev['delta']), Decimal('50.00'))
        self.assertEqual(prev['delta_pct'], 50.0)

    def test_invalid_week_param_returns_400(self):
        resp = self.client.get(self._url('not-a-date'))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cashier_forbidden(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.get(self._url('2026-06-10'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
