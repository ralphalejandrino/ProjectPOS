"""Weekly report integrity — the SILENT HOLE is closed (report-basis fix #2).

Previously the weekly report built completed days from finalized ZReports only
plus a today-only live snapshot, so a past day whose shift was never closed fell
through both buckets and was reported as a confident zero (status 'final').

The report now counts every sale on its true PHT day directly from the
transactions (_aggregate_transactions), whether or not the shift is finalized,
so an unclosed shift's sales no longer vanish. These tests pin that: the same
sale is counted whether the shift is open or closed.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from canteen.models import BusinessProfile, Item, Shift, User, PosTransaction
from canteen.services import create_pos_transaction, close_shift_and_finalize_z
from canteen.views import _weekly_payload


class WeeklyReportHoleTests(APITestCase):
    """A past day with an unclosed shift vanishes from the weekly report."""

    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='PROD', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.item = Item.objects.create(
            name='Brewed Coffee', price=Decimal('50.00'), stock=1000
        )
        # A week that is entirely in the past, so live_day is None and the
        # report is Z-only by construction (mirrors any completed week).
        today = timezone.localdate()
        self.week_start = today - timedelta(days=((today.weekday() - 5) % 7) + 14)
        self.week_end = self.week_start + timedelta(days=6)
        self.past_day = self.week_start + timedelta(days=3)

    def _sell(self, qty):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': qty}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )

    def _backdate(self, txn, d):
        """Move a transaction onto a past business day."""
        when = timezone.make_aware(
            timezone.datetime.combine(d, timezone.datetime.min.time())
        ) + timedelta(hours=10)
        PosTransaction.objects.filter(pk=txn.pk).update(created_at=when)

    def test_closed_shift_on_past_day_is_counted(self):
        """CONTROL: the same sale, with the shift CLOSED, does show up."""
        shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        txn = self._sell(3)  # 150.00
        self._backdate(txn, self.past_day)
        z = close_shift_and_finalize_z(shift.id, Decimal('0.00'), self.cashier)
        # Finalize stamps today's business_date; move the Z onto the past day.
        type(z).objects.filter(pk=z.pk).update(business_date=self.past_day)

        payload, err = _weekly_payload(self.past_day.strftime('%Y-%m-%d'))
        self.assertIsNone(err)
        self.assertEqual(
            Decimal(payload['summary']['gross_total']), Decimal('150.00'),
            'control failed: a closed shift must be counted',
        )

    def test_unclosed_shift_on_past_day_is_still_counted(self):
        """The fix: an unclosed shift's past-day sales are counted, not dropped."""
        Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        txn = self._sell(3)  # 150.00
        self._backdate(txn, self.past_day)

        # Ground truth: the sale is really in the database on that day.
        self.assertEqual(
            PosTransaction.objects.filter(
                created_at__date=self.past_day, void=False
            ).count(), 1,
        )

        payload, err = _weekly_payload(self.past_day.strftime('%Y-%m-%d'))
        self.assertIsNone(err)

        gross = Decimal(payload['summary']['gross_total'])
        day = next(
            d for d in payload['days']
            if d['date'] == self.past_day.strftime('%Y-%m-%d')
        )

        # The money is counted on its true day, even though the shift is open.
        self.assertEqual(gross, Decimal('150.00'))
        self.assertEqual(payload['summary']['transaction_count'], 1)
        self.assertEqual(Decimal(day['gross']), Decimal('150.00'))
        self.assertEqual(day['transaction_count'], 1)
