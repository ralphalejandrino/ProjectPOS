"""FEATURE-013 — multi-day / period report endpoint.

Aggregates across ZReports in a date range (manager/admin only). ZReports are
built seed-free at finalize time, so period totals exclude is_seed rows.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User, PosTransaction,
    Ingredient, IngredientUnit, IngredientRestockLog,
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

    def test_week_window_is_saturday_to_friday(self):
        """ISSUE-121-FU-A: any date inside the week snaps to the same Sat–Fri
        PHT window. 2026-06-06 is a Saturday → Sat 06-06 to Fri 06-12."""
        import datetime as dt
        for anchor in ('2026-06-06', '2026-06-08', '2026-06-12'):
            resp = self.client.get(self._url(anchor))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp.data['week_start'], '2026-06-06')  # Saturday
            self.assertEqual(resp.data['week_end'], '2026-06-12')    # Friday
            self.assertEqual(len(resp.data['days']), 7)
            self.assertEqual(resp.data['days'][0]['day_name'], 'Sat')
            self.assertEqual(resp.data['days'][6]['day_name'], 'Fri')
        start = dt.date(2026, 6, 6)
        self.assertEqual(start.weekday(), 5)  # Saturday

    def test_prev_next_week_steps_in_sat_fri_increments(self):
        """ISSUE-121-FU-A: stepping ±7 days from any date inside a week lands
        in the adjacent Sat–Fri window."""
        # Anchor inside Sat 06-06 → Fri 06-12.
        resp = self.client.get(self._url('2026-06-09'))
        self.assertEqual(resp.data['week_start'], '2026-06-06')
        self.assertEqual(resp.data['week_end'], '2026-06-12')
        # Previous week: anchor - 7 days.
        prev = self.client.get(self._url('2026-06-02'))
        self.assertEqual(prev.data['week_start'], '2026-05-30')
        self.assertEqual(prev.data['week_end'], '2026-06-05')
        # Next week: anchor + 7 days.
        nxt = self.client.get(self._url('2026-06-16'))
        self.assertEqual(nxt.data['week_start'], '2026-06-13')
        self.assertEqual(nxt.data['week_end'], '2026-06-19')

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

        # Relocate the prior sale one week back by its TRANSACTION date — the
        # report now buckets by created_at (report-basis fix #2), not by
        # ZReport.business_date. update() bypasses auto_now_add.
        PosTransaction.objects.all().update(
            created_at=timezone.now() - timedelta(days=7))
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
        """A week spanning Dec→Jan snaps to one Sat–Fri window across the
        year boundary (PHT). 2025-12-31 is a Wednesday → Sat 2025-12-27 to
        Fri 2026-01-02."""
        resp = self.client.get(self._url('2025-12-31'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['week_start'], '2025-12-27')
        self.assertEqual(resp.data['week_end'], '2026-01-02')
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
        # Healthy item — appears in no notice bucket.
        Item.objects.filter(pk=self.coffee.pk).update(
            stock=500, low_stock_threshold=10)
        low = Item.objects.create(name='Sugar Sachets', price=Decimal('1.00'),
                                  stock=5, low_stock_threshold=20)
        out = Item.objects.create(name='Oat Milk', price=Decimal('90.00'),
                                  stock=0, low_stock_threshold=5)

        resp = self.client.get(self._this_week())
        notices = resp.data['inventory_notices']

        low_names = [i['name'] for i in notices['low_stock']]
        self.assertIn('Sugar Sachets', low_names)
        self.assertNotIn('Oat Milk', low_names)        # out-of-stock listed once
        self.assertNotIn('Brewed Coffee', low_names)   # healthy

        self.assertEqual([i['name'] for i in notices['out_of_stock']], ['Oat Milk'])

    def test_inventory_notices_empty_states_present(self):
        """Section is never hidden: each bucket is an (empty) list, not absent."""
        Item.objects.filter(pk=self.coffee.pk).update(
            stock=500, low_stock_threshold=10)
        resp = self.client.get(self._this_week())
        notices = resp.data['inventory_notices']
        self.assertEqual(notices['low_stock'], [])
        self.assertEqual(notices['out_of_stock'], [])

    # --- ISSUE-121-FU-C: expiry notice removed from the report ----------

    def test_report_no_longer_includes_expiry_notice(self):
        """The expiring-soon field is gone from the weekly report payload
        (expiry logic elsewhere in the app is untouched)."""
        from datetime import timedelta
        from django.utils import timezone
        today = timezone.localdate()
        # An item that WOULD have shown under the old expiry notice.
        Item.objects.create(
            name='Fresh Cream', price=Decimal('60.00'), stock=8,
            low_stock_threshold=2, expiry_date=today + timedelta(days=5))
        resp = self.client.get(self._this_week())
        self.assertNotIn('expiring_soon', resp.data['inventory_notices'])

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

    # --- ISSUE-121-FU-B: restock cost (expense side) -------------------

    def _unit(self):
        unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'})
        return unit

    def test_restock_cost_sums_in_window_excludes_out_of_window(self):
        from django.utils import timezone
        from datetime import timedelta
        unit = self._unit()
        beans = Ingredient.objects.create(
            name='Beans', unit=unit, cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal('0'))
        milk = Ingredient.objects.create(
            name='Milk', unit=unit, cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal('0'))

        # In-window restocks: 10*5 = 50 (beans) + 2*3 = 6 (milk) = 56.
        IngredientRestockLog.objects.create(
            ingredient=beans, quantity_added=Decimal('10'),
            cost_per_unit=Decimal('5.0000'))
        IngredientRestockLog.objects.create(
            ingredient=milk, quantity_added=Decimal('2'),
            cost_per_unit=Decimal('3.0000'))
        # Out-of-window restock (30 days ago) — must NOT count.
        old = IngredientRestockLog.objects.create(
            ingredient=beans, quantity_added=Decimal('100'),
            cost_per_unit=Decimal('9.0000'))
        IngredientRestockLog.objects.filter(pk=old.pk).update(
            date=timezone.now() - timedelta(days=30))

        resp = self.client.get(self._this_week())
        rc = resp.data['restock_costs']
        self.assertEqual(Decimal(rc['total']), Decimal('56.00'))
        by_name = {r['name']: r for r in rc['by_ingredient']}
        self.assertEqual(Decimal(by_name['Beans']['cost']), Decimal('50.00'))
        self.assertEqual(Decimal(by_name['Milk']['cost']), Decimal('6.00'))
        # The 30-day-old beans restock is excluded from the beans line.
        self.assertNotIn('900', by_name['Beans']['cost'])

    def test_restock_cost_zero_when_no_restocks(self):
        resp = self.client.get(self._this_week())
        rc = resp.data['restock_costs']
        self.assertEqual(Decimal(rc['total']), Decimal('0.00'))
        self.assertEqual(rc['by_ingredient'], [])

    # --- ISSUE-121-FU-D: thermal output --------------------------------

    def test_weekly_report_thermal_output_contains_totals(self):
        """The thermal line builder produces output without error and the
        week's gross total + section headers appear on the paper."""
        from django.utils import timezone
        from canteen.views import _weekly_payload
        from canteen.receipt_service import build_weekly_report_lines

        self._open_shift()
        self._sell(self.coffee, 2)   # gross 100

        week = timezone.localdate().strftime('%Y-%m-%d')
        payload, error = _weekly_payload(week)
        self.assertIsNone(error)

        lines = build_weekly_report_lines(payload, self.bp)
        text = '\n'.join(lines)
        self.assertIn('WEEKLY PERFORMANCE', text)
        self.assertIn('SUMMARY', text)
        self.assertIn('RESTOCK COST', text)
        # Gross total 100.00 is rendered (ascii currency, no peso glyph).
        self.assertIn('100.00', text)

    def test_period_print_endpoint_queues(self):
        """POST to the print endpoint returns queued (printer disabled in
        tests, so the threaded print is a no-op but the endpoint still 200s)."""
        from django.utils import timezone
        week = timezone.localdate().strftime('%Y-%m-%d')
        resp = self.client.post(
            f'/api/canteen/reports/period/print/?week={week}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'print queued')

    def test_period_print_endpoint_rejects_bad_week(self):
        resp = self.client.post(
            '/api/canteen/reports/period/print/?week=not-a-date')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # --- ISSUE-121-FU-F: restock detail table ---------------------------

    def _beans(self):
        return Ingredient.objects.create(
            name='Beans', unit=self._unit(), cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal('0'))

    def test_restock_detail_rows_include_recorded_by_and_handle_null(self):
        beans = self._beans()
        # Row WITH a recorder.
        IngredientRestockLog.objects.create(
            ingredient=beans, quantity_added=Decimal('10'),
            cost_per_unit=Decimal('5.0000'), recorded_by=self.manager)
        # Row with NULL recorded_by (legacy row) — must render '—', not error.
        IngredientRestockLog.objects.create(
            ingredient=beans, quantity_added=Decimal('2'),
            cost_per_unit=Decimal('3.0000'), recorded_by=None)

        resp = self.client.get(self._this_week())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        detail = resp.data['restock_detail']
        self.assertEqual(len(detail), 2)
        # Every row carries the required fields.
        for k in ('date', 'ingredient', 'quantity', 'unit', 'cost', 'recorded_by'):
            self.assertIn(k, detail[0])
        recorders = {r['recorded_by'] for r in detail}
        self.assertIn('manager', recorders)   # the recorded row
        self.assertIn('—', recorders)          # the null row, rendered gracefully
        # cost is the historical snapshot quantity_added * cost_per_unit.
        costs = {r['cost'] for r in detail}
        self.assertIn('50.00', costs)          # 10 * 5
        self.assertIn('6.00', costs)           # 2 * 3
        # Unit abbreviation comes through for display.
        self.assertEqual(detail[0]['unit'], 'g')

    def test_restock_detail_empty_when_no_restocks(self):
        resp = self.client.get(self._this_week())
        self.assertEqual(resp.data['restock_detail'], [])

    # --- ISSUE-121-FU-G: net cash flow -----------------------------------

    def test_net_cash_flow_equals_sales_minus_restock(self):
        beans = self._beans()
        self._open_shift()
        self._sell(self.coffee, 4)   # net 200 (VAT disabled → net == gross)
        IngredientRestockLog.objects.create(
            ingredient=beans, quantity_added=Decimal('10'),
            cost_per_unit=Decimal('5.0000'))   # restock spend 50

        resp = self.client.get(self._this_week())
        s = resp.data['summary']
        rc = resp.data['restock_costs']
        expected = Decimal(s['net_total']) - Decimal(rc['total'])
        self.assertEqual(Decimal(resp.data['net_cash_flow']), expected)
        self.assertEqual(Decimal(resp.data['net_cash_flow']), Decimal('150.00'))

    def test_net_cash_flow_can_be_negative_on_heavy_restock(self):
        beans = self._beans()
        self._open_shift()
        self._sell(self.coffee, 1)   # net 50
        IngredientRestockLog.objects.create(
            ingredient=beans, quantity_added=Decimal('40'),
            cost_per_unit=Decimal('5.0000'))   # restock spend 200

        resp = self.client.get(self._this_week())
        # 50 − 200 = −150.00; negative is expected (cash basis, not profit).
        self.assertEqual(Decimal(resp.data['net_cash_flow']), Decimal('-150.00'))

    # --- ISSUE-121-FU-H: consistent thermal layout ----------------------

    def test_weekly_thermal_includes_restock_detail_and_cash_flow(self):
        from django.utils import timezone
        from canteen.views import _weekly_payload
        from canteen.receipt_service import build_weekly_report_lines

        beans = self._beans()
        self._open_shift()
        self._sell(self.coffee, 2)   # net 100
        IngredientRestockLog.objects.create(
            ingredient=beans, quantity_added=Decimal('3'),
            cost_per_unit=Decimal('4.0000'), recorded_by=self.manager)  # 12

        week = timezone.localdate().strftime('%Y-%m-%d')
        payload, error = _weekly_payload(week)
        self.assertIsNone(error)

        lines = build_weekly_report_lines(payload, self.bp)
        text = '\n'.join(lines)
        # FU-G headline + FU-F detail both present on the paper.
        self.assertIn('NET CASH FLOW', text)
        self.assertIn('RESTOCK DETAIL', text)
        self.assertIn('manager', text)         # the restocker's name in detail
        # Existing sections still present (no data removed by the FU-H reflow).
        self.assertIn('WEEKLY PERFORMANCE', text)
        self.assertIn('SUMMARY', text)
        self.assertIn('RESTOCK COST', text)

    def test_thermal_builders_share_layout_primitives(self):
        """FU-H: X/Z/Weekly share one set of layout helpers, so a divider is the
        full paper width and a KV row is padded to that width."""
        from canteen.receipt_service import (
            _thermal_rule, _thermal_kv, _thermal_center, _receipt_cols,
        )
        width = _receipt_cols(self.bp)
        # Divider spans the paper; '-' for sections, '=' for banners.
        self.assertEqual(len(_thermal_rule(width)), width)
        self.assertEqual(_thermal_rule(width)[0], '-')
        self.assertEqual(_thermal_rule(width, '=')[0], '=')
        # KV row: label left, value right, padded to full width.
        row = _thermal_kv('Net:', '100.00', width)
        self.assertEqual(len(row), width)
        self.assertTrue(row.startswith('Net:'))
        self.assertTrue(row.endswith('100.00'))
        # Section titles are centered within the paper width.
        centered = _thermal_center('SUMMARY', width)
        self.assertEqual(len(centered), width)
        self.assertIn('SUMMARY', centered)
        self.assertTrue(centered.startswith(' '))
