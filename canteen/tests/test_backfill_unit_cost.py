"""Tests for the backfill_unit_cost command, incl. the --include-zero /--since
extension (re-snapshot #10-style zero-cost lines after ingredient costs are
corrected). Negative controls pin that genuinely-costed lines, zero lines
without the flag, and out-of-window lines are never rewritten, and that sales
money fields are untouched."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from canteen.models import (
    BusinessProfile, Item, PosTransaction, PosTransactionItem, Shift, User,
)
from canteen.services import create_pos_transaction


class BackfillUnitCostTests(TestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        self.item = Item.objects.create(
            name='Brewed Coffee', price=Decimal('50.00'), stock=1000,
            purchase_price=Decimal('7.00'),
        )

    def _sell_line(self, unit_cost):
        """One-line sale with unit_cost forced to a known snapshot state."""
        txn = create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )
        PosTransactionItem.objects.filter(pos_transaction=txn).update(unit_cost=unit_cost)
        return PosTransactionItem.objects.get(pos_transaction=txn)

    def _run(self, *args):
        out, err = StringIO(), StringIO()
        call_command('backfill_unit_cost', *args, stdout=out, stderr=err)
        return out.getvalue() + err.getvalue()

    def test_null_line_backfilled_by_default(self):
        line = self._sell_line(None)
        out = self._run()
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('7.00'))
        self.assertIn('updated 1 of 1', out)

    def test_zero_line_untouched_without_flag(self):
        # Backward compat: the default run is NULL-only.
        line = self._sell_line(Decimal('0.0000'))
        out = self._run()
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('0.0000'))
        self.assertIn('Nothing to backfill', out)

    def test_include_zero_resnapshots_now_costed_line(self):
        line = self._sell_line(Decimal('0.0000'))
        out = self._run('--include-zero')
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('7.00'))
        self.assertIn('updated 1 of 1', out)

    def test_genuinely_costed_line_never_selected(self):
        # A >0 snapshot is history — must survive even --include-zero while the
        # item's current cost differs.
        line = self._sell_line(Decimal('5.0000'))
        out = self._run('--include-zero')
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('5.0000'))
        self.assertIn('Nothing to backfill', out)

    def test_include_zero_skips_still_uncosted_item(self):
        # Item still has no positive cost: a 0→0 rewrite would falsely mark the
        # line re-snapshotted. Must skip and say so.
        self.item.purchase_price = None
        self.item.save(update_fields=['purchase_price'])
        line = self._sell_line(Decimal('0.0000'))
        out = self._run('--include-zero')
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('0.0000'))
        self.assertIn('updated 0 of 1', out)
        self.assertIn('skipped 1 zero-cost line', out)

    def test_since_scopes_the_window(self):
        old = self._sell_line(Decimal('0.0000'))
        PosTransaction.objects.filter(id=old.pos_transaction_id).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        new = self._sell_line(Decimal('0.0000'))
        self._run('--include-zero', '--since', timezone.localdate().isoformat())
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(old.unit_cost, Decimal('0.0000'))  # out of window
        self.assertEqual(new.unit_cost, Decimal('7.00'))

    def test_since_rejects_garbage_date(self):
        with self.assertRaises(CommandError):
            self._run('--include-zero', '--since', '19-07-2026')

    def test_dry_run_writes_nothing(self):
        line = self._sell_line(Decimal('0.0000'))
        out = self._run('--include-zero', '--dry-run')
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('0.0000'))
        self.assertIn('would update 1 of 1', out)

    def test_money_fields_untouched(self):
        line = self._sell_line(Decimal('0.0000'))
        before = PosTransactionItem.objects.values(
            'id', 'quantity', 'unit_price', 'subtotal'
        ).get(id=line.id)
        txn_before = PosTransaction.objects.values(
            'gross_total', 'discount_total', 'net_total', 'vat_amount'
        ).get(id=line.pos_transaction_id)
        self._run('--include-zero')
        after = PosTransactionItem.objects.values(
            'id', 'quantity', 'unit_price', 'subtotal'
        ).get(id=line.id)
        txn_after = PosTransaction.objects.values(
            'gross_total', 'discount_total', 'net_total', 'vat_amount'
        ).get(id=line.pos_transaction_id)
        self.assertEqual(before, after)
        self.assertEqual(txn_before, txn_after)

    def test_idempotent(self):
        line = self._sell_line(Decimal('0.0000'))
        self._run('--include-zero')
        out = self._run('--include-zero')
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('7.00'))
        self.assertIn('Nothing to backfill', out)
