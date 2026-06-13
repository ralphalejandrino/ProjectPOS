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

    def test_week_window_crosses_year_boundary(self):
        """A week spanning Dec→Jan snaps to one Mon–Sun window across the
        year boundary (PHT). 2025-12-31 is a Wednesday → Mon 2025-12-29 to
        Sun 2026-01-04."""
        resp = self.client.get(self._url('2025-12-31'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['week_start'], '2025-12-29')
        self.assertEqual(resp.data['week_end'], '2026-01-04')
        self.assertEqual(len(resp.data['days']), 7)


class ISSUE121WeeklyReportTests(APITestCase):
    """ISSUE-121 — owner-facing weekly report additions: per-metric WoW
    (incl. avg ticket, null-safe), worst sellers, busiest hour, weekly
    cashier summary, and current-state inventory notices.

    These four sections (worst sellers / busiest hour / cashiers / notices)
    query PosTransaction and Item directly rather than ZReports, so they are
    exercised on the in-progress current week.
    """

    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.cashier2 = User.objects.create_user(
            username='cashier2', password='x', role='cashier'
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.coffee = Item.objects.create(
            name='Brewed Coffee', price=Decimal('50.00'), stock=1000
        )
        self.client.force_authenticate(self.manager)

    def _open_shift(self, cashier=None):
        return Shift.objects.create(
            cashier=cashier or self.cashier,
            opening_cash=Decimal('0.00'), is_open=True,
        )

    def _sell(self, item, qty, cashier=None):
        return create_pos_transaction(
            [{'item_id': item.id, 'quantity': qty}],
            'cash', cashier=cashier or self.cashier,
            cash_received=Decimal('1000.00'),
        )

    def _this_week(self):
        from django.utils import timezone
        return f"/api/canteen/reports/period/?week={timezone.localdate():%Y-%m-%d}"

    # --- Worst sellers ---------------------------------------------------

    def test_worst_sellers_rank_zero_sales_first(self):
        """Bottom 5 active items by units sold; never-sold items rank first."""
        slow = Item.objects.create(name='Almond Croissant',
                                   price=Decimal('80.00'), stock=50)
        never = Item.objects.create(name='Kale Smoothie',
                                    price=Decimal('120.00'), stock=50)
        self._open_shift()
        self._sell(self.coffee, 5)   # popular
        self._sell(slow, 1)          # near-zero

        resp = self.client.get(self._this_week())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        worst = resp.data['worst_sellers']
        self.assertEqual([w['name'] for w in worst],
                         ['Kale Smoothie', 'Almond Croissant', 'Brewed Coffee'])
        self.assertEqual(worst[0]['quantity'], 0)
        self.assertEqual(worst[1]['quantity'], 1)

    def test_worst_sellers_excludes_inactive_items(self):
        Item.objects.create(name='Retired Muffin', price=Decimal('40.00'),
                            stock=0, is_active=False)
        self._open_shift()
        self._sell(self.coffee, 2)
        resp = self.client.get(self._this_week())
        names = [w['name'] for w in resp.data['worst_sellers']]
        self.assertNotIn('Retired Muffin', names)

    # --- Busiest hour ----------------------------------------------------

    def test_busiest_hour_reports_peak_local_hour(self):
        from django.utils import timezone
        self._open_shift()
        t1 = self._sell(self.coffee, 1)
        t2 = self._sell(self.coffee, 1)
        t3 = self._sell(self.coffee, 1)
        now = timezone.localtime()
        # Two txns at 14:00 PHT, one at 09:00 PHT, all within the current week.
        nine = now.replace(hour=9, minute=0, second=0, microsecond=0)
        two = now.replace(hour=14, minute=0, second=0, microsecond=0)
        PosTransaction.objects.filter(pk__in=[t1.pk, t2.pk]).update(created_at=two)
        PosTransaction.objects.filter(pk=t3.pk).update(created_at=nine)

        resp = self.client.get(self._this_week())
        self.assertEqual(resp.data['busiest_hour'], {'hour': 14, 'count': 2})

    def test_busiest_hour_null_on_empty_week(self):
        resp = self.client.get(self._this_week())
        self.assertIsNone(resp.data['busiest_hour'])

    # --- Weekly cashier summary -----------------------------------------

    def test_cashier_summary_per_cashier_with_voids(self):
        self._open_shift(self.cashier)
        self._open_shift(self.cashier2)
        self._sell(self.coffee, 2, cashier=self.cashier)    # gross 100
        self._sell(self.coffee, 1, cashier=self.cashier2)   # gross 50
        voided = self._sell(self.coffee, 1, cashier=self.cashier2)
        PosTransaction.objects.filter(pk=voided.pk).update(void=True)

        resp = self.client.get(self._this_week())
        cashiers = {c['name']: c for c in resp.data['cashiers']}
        self.assertEqual(Decimal(cashiers['cashier']['gross']), Decimal('100.00'))
        self.assertEqual(cashiers['cashier']['txns'], 1)
        self.assertEqual(cashiers['cashier']['voids'], 0)
        self.assertEqual(Decimal(cashiers['cashier2']['gross']), Decimal('50.00'))
        self.assertEqual(cashiers['cashier2']['txns'], 1)
        self.assertEqual(cashiers['cashier2']['voids'], 1)

    # --- Inventory notices ----------------------------------------------

    def test_inventory_notices_thresholds(self):
        from django.utils import timezone
        from datetime import timedelta
        today = timezone.localdate()
        # Healthy item — appears in no notice bucket.
        Item.objects.filter(pk=self.coffee.pk).update(
            stock=500, low_stock_threshold=10)
        low = Item.objects.create(name='Sugar Sachets', price=Decimal('1.00'),
                                  stock=5, low_stock_threshold=20)
        out = Item.objects.create(name='Oat Milk', price=Decimal('90.00'),
                                  stock=0, low_stock_threshold=5)
        expiring = Item.objects.create(
            name='Fresh Cream', price=Decimal('60.00'), stock=8,
            low_stock_threshold=2, expiry_date=today + timedelta(days=5))
        expired = Item.objects.create(
            name='Day-old Pastry', price=Decimal('30.00'), stock=3,
            low_stock_threshold=1, expiry_date=today - timedelta(days=1))
        # Beyond the 14-day horizon — excluded.
        Item.objects.create(
            name='Canned Goods', price=Decimal('25.00'), stock=40,
            low_stock_threshold=5, expiry_date=today + timedelta(days=60))

        resp = self.client.get(self._this_week())
        notices = resp.data['inventory_notices']

        low_names = [i['name'] for i in notices['low_stock']]
        self.assertIn('Sugar Sachets', low_names)
        self.assertNotIn('Oat Milk', low_names)        # out-of-stock listed once
        self.assertNotIn('Brewed Coffee', low_names)   # healthy

        self.assertEqual([i['name'] for i in notices['out_of_stock']], ['Oat Milk'])

        expiry_names = [i['name'] for i in notices['expiring_soon']]
        self.assertIn('Fresh Cream', expiry_names)
        self.assertIn('Day-old Pastry', expiry_names)  # already expired included
        self.assertNotIn('Canned Goods', expiry_names)
        day_old = next(i for i in notices['expiring_soon']
                       if i['name'] == 'Day-old Pastry')
        self.assertEqual(day_old['days_left'], -1)

    def test_inventory_notices_empty_states_present(self):
        """Section is never hidden: each bucket is an (empty) list, not absent."""
        Item.objects.filter(pk=self.coffee.pk).update(
            stock=500, low_stock_threshold=10)
        resp = self.client.get(self._this_week())
        notices = resp.data['inventory_notices']
        self.assertEqual(notices['low_stock'], [])
        self.assertEqual(notices['expiring_soon'], [])
        self.assertEqual(notices['out_of_stock'], [])

    # --- Per-metric WoW (avg ticket null-safety) ------------------------

    def test_avg_ticket_present_and_wow_nullsafe_without_prior_week(self):
        self._open_shift()
        self._sell(self.coffee, 2)   # one txn, gross 100 → avg 100
        resp = self.client.get(self._this_week())
        self.assertEqual(Decimal(resp.data['summary']['avg_ticket']),
                         Decimal('100.00'))
        # No prior week planted → WoW baseline omitted, never a crash.
        self.assertIsNone(resp.data['previous_week'])

    def test_avg_ticket_null_on_empty_week(self):
        resp = self.client.get(self._this_week())
        self.assertIsNone(resp.data['summary']['avg_ticket'])
