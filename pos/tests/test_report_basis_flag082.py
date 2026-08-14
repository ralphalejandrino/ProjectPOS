"""FLAG-082 — the weekly report must use ONE reporting basis.

The flag described a split: headline gross/net read from finalized ZReport
rows, while FEATURE-054's COGS/profit/reorder read live PosTransactionItem —
so those included unclosed-shift sales and the headline did not.

Report-basis fix (#2) already moved the whole money path onto
_aggregate_transactions. These tests PIN that, because the failure is silent:
if anything reverts a figure to a ZReport read, the numbers stay plausible and
merely stop agreeing with each other.

The case that makes it concrete is PROD's own: shifts there routinely run 14-31
hours. ZReport.business_date is the shift's OPEN date, so a shift spanning
midnight files a whole day's sales under the wrong day.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone as dj_tz
from rest_framework.test import APITestCase

from pos.models import BusinessProfile, Item, Shift, User
from pos.services import close_shift_and_finalize_z, create_pos_transaction


class WeeklyReportBasisTests(APITestCase):
    def setUp(self):
        bp = BusinessProfile.get_instance()
        bp.vat_enabled = False
        bp.track_inventory = False
        bp.printer_mode = 'disabled'
        bp.save()
        self.manager = User.objects.create_user(
            username='m1', password='x', role='manager')
        self.cashier = User.objects.create_user(
            username='c1', password='x', role='cashier')
        self.item = Item.objects.create(
            name='Latte', price=Decimal('100.00'), stock=10000)
        self.client.force_authenticate(user=self.manager)

    def _week_url(self, d=None):
        d = d or dj_tz.localdate()
        return f"/api/pos/reports/period/?week={d.strftime('%Y-%m-%d')}"

    def _sell(self, shift=None):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}],
            'cash', cashier=self.cashier,
        )

    def test_sales_in_an_OPEN_shift_appear_in_the_weekly_report(self):
        """🔴 The heart of FLAG-082. A ZReport-based basis cannot see these at
        all, because no Z exists until the shift closes — and PROD shifts stay
        open for a day at a time."""
        Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('2000.00'), is_open=True)
        self._sell()

        res = self.client.get(self._week_url())

        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(str(res.data['summary']['gross_total'])),
                         Decimal('100.00'))

    def test_closing_the_shift_does_not_change_the_figure(self):
        """The same sale must total the same before and after finalization.
        If the basis were mixed, closing would move the number."""
        shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('2000.00'), is_open=True)
        self._sell()

        before = self.client.get(self._week_url()).data['summary']['gross_total']
        close_shift_and_finalize_z(shift.id, Decimal('2100.00'), self.cashier)
        after = self.client.get(self._week_url()).data['summary']['gross_total']

        self.assertEqual(Decimal(str(before)), Decimal(str(after)))
        self.assertEqual(Decimal(str(after)), Decimal('100.00'))

    def test_NEGATIVE_CONTROL_a_week_with_no_sales_reports_zero(self):
        """Proves the assertions above read real data rather than a constant."""
        past = dj_tz.localdate() - timedelta(days=90)
        res = self.client.get(self._week_url(past))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(str(res.data['summary']['gross_total'])),
                         Decimal('0'))

    def test_daily_rows_attribute_the_sale_to_today(self):
        Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('2000.00'), is_open=True)
        self._sell()

        res = self.client.get(self._week_url())
        today = dj_tz.localdate().strftime('%Y-%m-%d')
        row = [d for d in res.data['days'] if d['date'] == today]

        self.assertEqual(len(row), 1)
        self.assertEqual(Decimal(str(row[0]['gross'])), Decimal('100.00'))
