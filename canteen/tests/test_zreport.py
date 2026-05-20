"""FEATURE-011-C — ZReport finalization.

Aggregation is verified against hand-computed values derived from the
frozen PosTransaction columns (see test_pos_transaction.py for the
per-scenario column math).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone as dj_tz
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, PosTransaction, Shift, User, ZCounter, ZReport,
)
from canteen.services import close_shift_and_finalize_z, create_pos_transaction


def _bp(**overrides):
    bp = BusinessProfile.get_instance()
    bp.vat_enabled = True
    bp.vat_inclusive = True
    bp.vat_rate = Decimal('12.00')
    bp.sc_discount_rate = Decimal('20.00')
    bp.promo_discount_enabled = True
    bp.track_inventory = True
    bp.printer_mode = 'disabled'
    bp.machine_identification_number = 'MIN-001'
    bp.machine_serial_number = 'SN-001'
    bp.business_name = 'Test Canteen'
    bp.tin = '123-456-789'
    bp.address = 'Manila'
    for k, v in overrides.items():
        setattr(bp, k, v)
    bp.save()
    return bp


class ZReportTestBase(APITestCase):
    def setUp(self):
        self.bp = _bp()
        self.user = User.objects.create_user(
            username='cash1', password='x', role='cashier'
        )
        self.item = Item.objects.create(name='Coffee', price=Decimal('100.00'), stock=10000)

    def _open_shift(self, opening=Decimal('100.00')):
        return Shift.objects.create(
            cashier=self.user, opening_cash=opening, is_open=True
        )

    def _sell(self, payment_method='cash', price=None, **kwargs):
        item = self.item
        if price is not None:
            item = Item.objects.create(name=f'I{price}', price=Decimal(str(price)), stock=10000)
        return create_pos_transaction(
            [{'item_id': item.id, 'quantity': 1}],
            payment_method, cashier=self.user, **kwargs
        )


class AggregationMathTests(ZReportTestBase):
    def test_every_field_matches_hand_computed(self):
        """1. 5 non-voided + 1 voided across cash/gcash/card, SC + promo."""
        self._open_shift(Decimal('100.00'))
        self._sell('cash')                         # net 100
        self._sell('gcash')                        # net 100
        self._sell('card')                         # net 100
        self._sell('cash', price='112.00',
                   discount_amount=Decimal('20.00'), discount_type='sc',
                   discount_id_number='SC-1')      # SC: net 80, exempt 80
        self._sell('cash', discount_amount=Decimal('50.00'),
                   discount_type='promo')          # promo: net 50
        t6 = self._sell('cash')                    # will be voided
        PosTransaction.objects.filter(pk=t6.pk).update(
            voided_at=dj_tz.now(), void=True, status='void'
        )

        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('330.00'), self.user)

        self.assertEqual(z.transaction_count, 5)
        self.assertEqual(z.voided_count, 1)
        self.assertEqual(z.gross_sales, Decimal('512.00'))
        self.assertEqual(z.discount_total, Decimal('70.00'))
        self.assertEqual(z.net_sales, Decimal('430.00'))
        self.assertEqual(z.vat_exempt_sales, Decimal('80.00'))
        self.assertEqual(z.vatable_sales, Decimal('312.51'))
        self.assertEqual(z.zero_rated_sales, Decimal('0.00'))
        self.assertEqual(z.output_vat, Decimal('37.49'))
        self.assertEqual(z.sc_discount_total, Decimal('20.00'))
        self.assertEqual(z.pwd_discount_total, Decimal('0.00'))
        self.assertEqual(z.promo_discount_total, Decimal('50.00'))
        self.assertEqual(z.payment_breakdown['cash'], '230.00')
        self.assertEqual(z.payment_breakdown['gcash'], '100.00')
        self.assertEqual(z.payment_breakdown['card'], '100.00')
        self.assertEqual(z.payment_breakdown['maya'], '0.00')
        self.assertEqual(z.opening_cash, Decimal('100.00'))
        self.assertEqual(z.cash_collected, Decimal('230.00'))
        self.assertEqual(z.cash_expected, Decimal('330.00'))
        self.assertEqual(z.over_short, Decimal('0.00'))
        self.assertEqual(z.business_name, 'Test Canteen')
        self.assertEqual(z.machine_identification_number, 'MIN-001')
        self.assertEqual(len(z.voided_or_numbers), 1)
        self.assertEqual(z.voided_or_numbers[0], t6.transaction_no)


class ImmutabilityTests(ZReportTestBase):
    def test_zreport_save_raises(self):
        """2. ZReport is immutable."""
        self._open_shift()
        self._sell('cash')
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('200.00'), self.user)
        z.business_name = 'tampered'
        z.is_official = not z.is_official  # ISSUE-105: also immutable
        with self.assertRaises(ValidationError):
            z.save()


class CounterMonotonicityTests(ZReportTestBase):
    def test_sequential_closes_increment_and_accumulate(self):
        """3. 3 closes → z_counter 1,2,3; grand_total accumulates."""
        results = []
        for _ in range(3):
            self._open_shift()
            self._sell('cash')  # net 100 each
            shift = Shift.objects.get(cashier=self.user, is_open=True)
            results.append(close_shift_and_finalize_z(
                shift.id, Decimal('200.00'), self.user
            ))
        self.assertEqual([r.z_counter for r in results], [1, 2, 3])
        self.assertEqual(results[0].grand_total_sales, Decimal('100.00'))
        self.assertEqual(results[1].grand_total_sales, Decimal('200.00'))
        self.assertEqual(results[2].grand_total_sales, Decimal('300.00'))
        self.assertEqual(ZCounter.objects.get(pk=1).grand_total, Decimal('300.00'))


class CounterWrapTests(ZReportTestBase):
    def test_wrap_at_9999(self):
        """4. z_counter 9999 → next is 1, reset increments."""
        ZCounter.objects.create(pk=1, z_counter=9999, reset_counter=0,
                                 grand_total=Decimal('0'))
        self._open_shift()
        self._sell('cash')
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('200.00'), self.user)
        self.assertEqual(z.z_counter, 1)
        self.assertEqual(z.reset_counter, 1)


class ConcurrentCloseTests(ZReportTestBase):
    def test_double_close_same_shift_raises(self):
        """5. Second close of the same shift raises (already closed).

        NOTE: SQLite (the test DB) does not provide true row-level
        SELECT FOR UPDATE blocking, so genuine thread-concurrency cannot
        be exercised deterministically here. The atomic + select_for_update
        guarantees correctness on Postgres/MySQL; this test instead
        verifies the application-level "already closed" guard, which is
        the observable outcome of a lost concurrent race.
        """
        self._open_shift()
        self._sell('cash')
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        close_shift_and_finalize_z(shift.id, Decimal('200.00'), self.user)
        with self.assertRaises(ValidationError):
            close_shift_and_finalize_z(shift.id, Decimal('200.00'), self.user)


class FrozenColumnsRegressionTests(ZReportTestBase):
    def test_void_after_z_does_not_change_totals(self):
        """6. Voiding a transaction after Z is created (ISSUE-075)."""
        self._open_shift()
        self._sell('cash')
        self._sell('cash')
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('300.00'), self.user)
        gross_before = z.gross_sales
        net_before = z.net_sales

        # Void one of the (already finalized) transactions afterwards.
        any_txn = PosTransaction.objects.filter(shift=shift).first()
        PosTransaction.objects.filter(pk=any_txn.pk).update(
            voided_at=dj_tz.now(), void=True, status='void'
        )

        z.refresh_from_db()
        self.assertEqual(z.gross_sales, gross_before)
        self.assertEqual(z.net_sales, net_before)


class CashOverShortTests(ZReportTestBase):
    def test_opening_cash_included(self):
        """7. opening 100, cash sales 500, counted 605 → over_short +5."""
        self._open_shift(Decimal('100.00'))
        self._sell('cash', price='500.00')  # net 500
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('605.00'), self.user)
        self.assertEqual(z.opening_cash, Decimal('100.00'))
        self.assertEqual(z.cash_collected, Decimal('500.00'))
        self.assertEqual(z.cash_expected, Decimal('600.00'))
        self.assertEqual(z.over_short, Decimal('5.00'))


class PaymentBreakdownTests(ZReportTestBase):
    def test_card_present_in_breakdown(self):
        """8. Card payments are not dropped (ISSUE-081)."""
        self._open_shift()
        self._sell('card', price='250.00')
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, None, self.user)
        self.assertIn('card', z.payment_breakdown)
        self.assertEqual(z.payment_breakdown['card'], '250.00')
        self.assertIsNone(z.over_short)  # cash_counted not provided


class MinValidationTests(ZReportTestBase):
    def test_blank_min_finalizes_unofficial(self):
        """9. ISSUE-105: blank MIN → succeeds, is_official=False."""
        _bp(machine_identification_number='')
        self._open_shift()
        self._sell('cash')
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('200.00'), self.user)
        self.assertFalse(z.is_official)
        self.assertEqual(z.machine_identification_number, '')

    def test_nonblank_min_finalizes_official(self):
        """9b. ISSUE-105: non-blank MIN → is_official=True."""
        _bp(machine_identification_number='MIN-001')
        self._open_shift()
        self._sell('cash')
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('200.00'), self.user)
        self.assertTrue(z.is_official)
        self.assertEqual(z.machine_identification_number, 'MIN-001')


class ShiftClosureSideEffectTests(ZReportTestBase):
    def test_shift_closed_and_linked(self):
        """10. Shift closed_at/is_open/closing_cash + z_report back-ref."""
        self._open_shift()
        self._sell('cash')
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('150.00'), self.user)
        shift.refresh_from_db()
        self.assertIsNotNone(shift.closed_at)
        self.assertFalse(shift.is_open)
        self.assertEqual(shift.closing_cash, Decimal('150.00'))
        self.assertEqual(shift.z_report, z)
