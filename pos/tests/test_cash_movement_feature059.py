"""FEATURE-059 — cash-movement ledger + credit tender.

Why this exists, concretely: on 2026-08-01 PROD's Z29 read a ₱312.00 short.
The cause was `OR-20260801-0232` — `Milk Tea Classic x8 @ ₱39` — rung up as
CASH for a clinic that took the drinks and did not pay. Because the sale was
recorded as cash, `cash_expected` rose by ₱312 while no money entered the
drawer, so the shortfall landed on whoever closed the shift. The POS had no
way to say "sold, not paid".

The same blind spot covered two other real cases: the owner collecting cash
mid-shift, and a restock paid out of the drawer (FLAG-081).

Every assertion below is paired with a NEGATIVE CONTROL — the same scenario
with the feature not used — so a test that would pass vacuously fails loudly.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, CashMovement, Item, Shift, User,
)
from pos.services import close_shift_and_finalize_z, create_pos_transaction


def _bp():
    bp = BusinessProfile.get_instance()
    bp.vat_enabled = False
    bp.track_inventory = False
    bp.printer_mode = 'disabled'
    bp.business_name = 'PROD Test'
    bp.save()
    return bp


class CashMovementTestBase(APITestCase):
    def setUp(self):
        self.bp = _bp()
        self.cashier = User.objects.create_user(
            username='cash1', password='x', role='cashier'
        )
        self.manager = User.objects.create_user(
            username='mgr1', password='x', role='manager'
        )
        # ₱39 milk tea — the real PROD price on the incident transaction.
        self.milk_tea = Item.objects.create(
            name='Milk Tea - Classic', price=Decimal('39.00'), stock=100000
        )

    def _open_shift(self, opening=Decimal('2000.00')):
        """₱2,000 float — PROD's actual standing opening cash."""
        return Shift.objects.create(
            cashier=self.cashier, opening_cash=opening, is_open=True
        )

    def _sell(self, method, qty=1, shift=None):
        return create_pos_transaction(
            [{'item_id': self.milk_tea.id, 'quantity': qty}],
            method, cashier=self.cashier,
        )

    def _close(self, shift, counted):
        return close_shift_and_finalize_z(shift.id, counted, self.cashier)


class CreditTenderTests(CashMovementTestBase):
    """A credit tender must never reach cash_expected."""

    def test_credit_sale_does_not_inflate_cash_expected(self):
        shift = self._open_shift()
        self._sell('credit', qty=8)  # ₱312 — the real incident order

        z = self._close(shift, Decimal('2000.00'))

        # Nothing was paid, so the drawer should still hold only the float.
        self.assertEqual(z.cash_expected, Decimal('2000.00'))
        self.assertEqual(z.cash_collected, Decimal('0.00'))
        # And the count of exactly the float must reconcile to zero.
        self.assertEqual(z.over_short, Decimal('0.00'))

    def test_NEGATIVE_CONTROL_same_sale_as_cash_does_inflate(self):
        """The control that proves the test above is not vacuous.

        Identical order, tendered as cash: cash_expected MUST rise by ₱312 and
        counting only the float MUST produce the −312.00 that PROD actually saw.
        If this fails, the assertion above proves nothing.
        """
        shift = self._open_shift()
        self._sell('cash', qty=8)

        z = self._close(shift, Decimal('2000.00'))

        self.assertEqual(z.cash_expected, Decimal('2312.00'))
        self.assertEqual(z.over_short, Decimal('-312.00'))

    def test_credit_is_reported_not_hidden(self):
        """Credit must be visible, not silently dropped — an invisible
        receivable is how the shop forgets it is owed money."""
        shift = self._open_shift()
        self._sell('credit', qty=8)

        z = self._close(shift, Decimal('2000.00'))

        # FEATURE-065 split this field: `credit_extended` is now CHARGE only,
        # and this sale takes no explicit kind so the shop default ('house')
        # applies. The property under test is unchanged and asserted
        # kind-agnostically — the ₱312 must be reported SOMEWHERE, whichever
        # bucket it lands in. Silently dropping it is still the failure.
        self.assertEqual(
            z.credit_extended + z.house_consumption, Decimal('312.00')
        )
        self.assertEqual(z.house_consumption, Decimal('312.00'))
        self.assertEqual(z.payment_breakdown.get('credit'), '312.00')

    def test_mixed_cash_and_credit_splits_correctly(self):
        shift = self._open_shift()
        self._sell('cash', qty=1)     # ₱39 real money
        self._sell('credit', qty=8)   # ₱312 owed

        z = self._close(shift, Decimal('2039.00'))

        self.assertEqual(z.cash_collected, Decimal('39.00'))
        self.assertEqual(z.cash_expected, Decimal('2039.00'))
        # FEATURE-065: charge + house, so this stays true regardless of which
        # kind the shop defaults to. What matters here is that the ₱312 never
        # reached cash_expected.
        self.assertEqual(
            z.credit_extended + z.house_consumption, Decimal('312.00')
        )
        self.assertEqual(z.over_short, Decimal('0.00'))
        # Gross sales still counts the credit sale — goods did leave.
        self.assertEqual(z.gross_sales, Decimal('351.00'))


class CashMovementReconciliationTests(CashMovementTestBase):
    """Drops, payouts and settlements move cash_expected in the right
    direction and by the right magnitude."""

    def test_drop_reduces_cash_expected(self):
        """The owner collecting ₱500 mid-shift — the likely cause of PROD's
        unexplained −477 and −370."""
        shift = self._open_shift()
        self._sell('cash', qty=10)  # ₱390
        CashMovement.objects.create(
            shift=shift, kind=CashMovement.KIND_DROP,
            amount=Decimal('500.00'), reason='Owner collected',
        )

        z = self._close(shift, Decimal('1890.00'))  # 2000 + 390 - 500

        self.assertEqual(z.cash_expected, Decimal('1890.00'))
        self.assertEqual(z.cash_paid_out, Decimal('500.00'))
        self.assertEqual(z.over_short, Decimal('0.00'))

    def test_NEGATIVE_CONTROL_without_the_drop_it_reads_short(self):
        """Same money, same count, no drop recorded → the old behaviour: a
        500 short blamed on nobody. Proves the drop is doing the work."""
        shift = self._open_shift()
        self._sell('cash', qty=10)

        z = self._close(shift, Decimal('1890.00'))

        self.assertEqual(z.cash_expected, Decimal('2390.00'))
        self.assertEqual(z.over_short, Decimal('-500.00'))

    def test_payout_reduces_cash_expected_FLAG081(self):
        """FLAG-081: a restock paid out of the drawer."""
        shift = self._open_shift()
        CashMovement.objects.create(
            shift=shift, kind=CashMovement.KIND_PAYOUT,
            amount=Decimal('250.00'), reason='Milk restock',
        )

        z = self._close(shift, Decimal('1750.00'))

        self.assertEqual(z.cash_expected, Decimal('1750.00'))
        self.assertEqual(z.cash_paid_out, Decimal('250.00'))
        self.assertEqual(z.over_short, Decimal('0.00'))

    def test_settlement_increases_cash_expected(self):
        """The clinic pays its ₱312 tab on a later shift: money arrives with
        no sale attached to it."""
        shift = self._open_shift()
        credit_txn = self._sell('credit', qty=8)
        settle_shift = shift
        CashMovement.objects.create(
            shift=settle_shift, kind=CashMovement.KIND_SETTLEMENT,
            amount=Decimal('312.00'), reason='Clinic paid tab',
            settles=credit_txn,
        )

        z = self._close(settle_shift, Decimal('2312.00'))

        self.assertEqual(z.cash_expected, Decimal('2312.00'))
        self.assertEqual(z.cash_paid_in, Decimal('312.00'))
        self.assertEqual(z.credit_settled, Decimal('312.00'))
        self.assertEqual(z.over_short, Decimal('0.00'))

    def test_cash_in_increases_cash_expected(self):
        shift = self._open_shift()
        CashMovement.objects.create(
            shift=shift, kind=CashMovement.KIND_CASH_IN,
            amount=Decimal('1000.00'), reason='Extra float',
        )

        z = self._close(shift, Decimal('3000.00'))

        self.assertEqual(z.cash_expected, Decimal('3000.00'))
        self.assertEqual(z.cash_paid_in, Decimal('1000.00'))

    def test_movements_net_against_each_other(self):
        shift = self._open_shift()
        self._sell('cash', qty=10)  # +390
        for kind, amt in (
            (CashMovement.KIND_DROP, '300.00'),
            (CashMovement.KIND_PAYOUT, '100.00'),
            (CashMovement.KIND_CASH_IN, '50.00'),
        ):
            CashMovement.objects.create(
                shift=shift, kind=kind, amount=Decimal(amt), reason='x'
            )

        z = self._close(shift, Decimal('2040.00'))  # 2000+390-300-100+50

        self.assertEqual(z.cash_paid_out, Decimal('400.00'))
        self.assertEqual(z.cash_paid_in, Decimal('50.00'))
        self.assertEqual(z.cash_expected, Decimal('2040.00'))
        self.assertEqual(z.over_short, Decimal('0.00'))

    def test_signed_amount_direction(self):
        shift = self._open_shift()
        out = CashMovement(
            shift=shift, kind=CashMovement.KIND_DROP, amount=Decimal('10.00')
        )
        inn = CashMovement(
            shift=shift, kind=CashMovement.KIND_CASH_IN, amount=Decimal('10.00')
        )
        self.assertEqual(out.signed_amount, Decimal('-10.00'))
        self.assertEqual(inn.signed_amount, Decimal('10.00'))

    def test_movements_on_another_shift_do_not_leak(self):
        """Negative control on scoping: a movement belongs to exactly one
        shift and must not shift another shift's expected figure."""
        shift_a = self._open_shift()
        other_cashier = User.objects.create_user(
            username='cash2', password='x', role='cashier'
        )
        shift_b = Shift.objects.create(
            cashier=other_cashier, opening_cash=Decimal('2000.00'), is_open=True
        )
        CashMovement.objects.create(
            shift=shift_b, kind=CashMovement.KIND_DROP,
            amount=Decimal('999.00'), reason='other shift',
        )

        z = self._close(shift_a, Decimal('2000.00'))

        self.assertEqual(z.cash_expected, Decimal('2000.00'))
        self.assertEqual(z.cash_paid_out, Decimal('0.00'))
        self.assertEqual(z.over_short, Decimal('0.00'))


class CashMovementApiTests(CashMovementTestBase):
    """The endpoint's guards. These matter as much as the arithmetic: a
    freely-writable payout is a way to erase any shortage."""

    def _url(self, shift):
        return f'/api/pos/shifts/{shift.id}/cash-movements/'

    def test_cashier_CANNOT_record_a_movement(self):
        """🔴 The control that keeps this feature from becoming the hole it
        was built to close."""
        shift = self._open_shift()
        self.client.force_authenticate(user=self.cashier)

        res = self.client.post(self._url(shift), {
            'kind': 'payout', 'amount': '500.00', 'reason': 'x',
        }, format='json')

        self.assertEqual(res.status_code, 403)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_manager_CAN_record_a_movement(self):
        """Negative control for the permission test above — proves the 403 is
        about role, not a broken endpoint."""
        shift = self._open_shift()
        self.client.force_authenticate(user=self.manager)

        res = self.client.post(self._url(shift), {
            'kind': 'payout', 'amount': '500.00', 'reason': 'Milk restock',
        }, format='json')

        self.assertEqual(res.status_code, 201)
        self.assertEqual(CashMovement.objects.count(), 1)
        self.assertEqual(CashMovement.objects.get().created_by, self.manager)

    def test_outflow_requires_a_reason(self):
        shift = self._open_shift()
        self.client.force_authenticate(user=self.manager)

        res = self.client.post(self._url(shift), {
            'kind': 'drop', 'amount': '500.00',
        }, format='json')

        self.assertEqual(res.status_code, 400)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_non_positive_amount_rejected(self):
        shift = self._open_shift()
        self.client.force_authenticate(user=self.manager)
        for bad in ('0', '-5.00'):
            res = self.client.post(self._url(shift), {
                'kind': 'drop', 'amount': bad, 'reason': 'x',
            }, format='json')
            self.assertEqual(res.status_code, 400, bad)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_bad_kind_rejected(self):
        shift = self._open_shift()
        self.client.force_authenticate(user=self.manager)
        res = self.client.post(self._url(shift), {
            'kind': 'withdrawal', 'amount': '5.00', 'reason': 'x',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_cannot_record_against_a_closed_shift(self):
        """A closed shift already produced an immutable Z; a later movement
        would silently contradict a report that has been printed."""
        shift = self._open_shift()
        self._close(shift, Decimal('2000.00'))
        self.client.force_authenticate(user=self.manager)

        res = self.client.post(self._url(shift), {
            'kind': 'drop', 'amount': '10.00', 'reason': 'late',
        }, format='json')

        self.assertEqual(res.status_code, 400)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_settlement_requires_a_transaction(self):
        shift = self._open_shift()
        self.client.force_authenticate(user=self.manager)
        res = self.client.post(self._url(shift), {
            'kind': 'settlement', 'amount': '312.00', 'reason': 'clinic',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_settles_rejected_on_non_settlement(self):
        shift = self._open_shift()
        txn = self._sell('credit', qty=8)
        self.client.force_authenticate(user=self.manager)
        res = self.client.post(self._url(shift), {
            'kind': 'drop', 'amount': '10.00', 'reason': 'x',
            'settles': txn.id,
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_list_returns_recorded_movements(self):
        shift = self._open_shift()
        CashMovement.objects.create(
            shift=shift, kind=CashMovement.KIND_DROP,
            amount=Decimal('100.00'), reason='Owner collected',
        )
        self.client.force_authenticate(user=self.manager)

        res = self.client.get(self._url(shift))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['signed_amount'], '-100.00')
        self.assertEqual(res.data[0]['reason'], 'Owner collected')
