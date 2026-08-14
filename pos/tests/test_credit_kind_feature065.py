"""FEATURE-065 — house consumption vs a real charge.

Ralph, 2026-08-14: PROD's clinic tab will almost certainly never be paid — the
family that runs the clinic owns the cafe — and "regarding credit default it
to house."

A debt that will never be collected is not a receivable, it is consumption,
and calling it a receivable breaks three things at once:

  * "Credit Extended" accumulates forever and never clears, so the A/R figure
    becomes fiction and the owner learns to ignore it.
  * Net sales carries revenue that will never arrive.
  * The milk and cups really were used, so COGS is real while the matching
    revenue is not — the margin reads better than it is, which quietly
    corrupts the July costing repair.

Defaulting to house is PROD's rule, NOT the product's: on a shop that runs
genuine collectible tabs the same default would silently write off real
debts. So it is read from BusinessProfile, and that is asserted here.

The BIR path is deliberately untouched — a house sale is still a sale with an
OR. This is a management-reporting split only.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import BusinessProfile, Item, PaymentLine, Shift, User, ZReport
from pos.services import (
    close_shift_and_finalize_z,
    create_pos_transaction,
    credit_lines_for_shift,
)


class CreditKindTestBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.get_instance()
        self.bp.vat_enabled = False
        self.bp.track_inventory = False
        self.bp.printer_mode = 'disabled'
        self.bp.business_name = 'PROD Test'
        self.bp.default_credit_kind = 'house'
        self.bp.save()
        self.cashier = User.objects.create_user(
            username='cash1', password='x', role='cashier'
        )
        self.milk_tea = Item.objects.create(
            name='Milk Tea - Classic', price=Decimal('39.00'), stock=100000
        )

    def _open_shift(self, opening=Decimal('2000.00')):
        return Shift.objects.create(
            cashier=self.cashier, opening_cash=opening, is_open=True
        )

    def _sell_credit(self, qty=8, kind=None, note='Clinic'):
        line = {'method': 'credit', 'amount': str(Decimal('39.00') * qty),
                'note': note}
        if kind is not None:
            line['credit_kind'] = kind
        return create_pos_transaction(
            [{'item_id': self.milk_tea.id, 'quantity': qty}],
            'credit', cashier=self.cashier, payment_lines=[line],
        )


class DefaultKindTests(CreditKindTestBase):

    def test_credit_defaults_to_house(self):
        """Ralph's call, and PROD's reality."""
        self._open_shift()
        self._sell_credit()

        self.assertEqual(PaymentLine.objects.get(method='credit').credit_kind,
                         'house')

    def test_the_default_comes_from_the_SHOP_not_a_constant(self):
        """🔑 The property that keeps this from being a hardcoded PROD rule.

        A business that runs genuine collectible tabs flips one setting and
        gets 'charge' without a code change. If this ever regresses to a
        constant, that shop silently writes off every debt it is owed.
        """
        self.bp.default_credit_kind = 'charge'
        self.bp.save()
        self._open_shift()

        self._sell_credit()

        self.assertEqual(PaymentLine.objects.get(method='credit').credit_kind,
                         'charge')

    def test_an_explicit_kind_wins_over_the_default(self):
        self._open_shift()
        self._sell_credit(kind='charge')
        self.assertEqual(PaymentLine.objects.get(method='credit').credit_kind,
                         'charge')

    def test_a_junk_kind_falls_back_to_the_default_and_does_not_reject(self):
        """A bad kind must never be the thing that blocks a sale on a live
        register — same reasoning as the note truncation in FEATURE-059-FU."""
        self._open_shift()
        self._sell_credit(kind='nonsense')
        self.assertEqual(PaymentLine.objects.get(method='credit').credit_kind,
                         'house')

    def test_NEGATIVE_CONTROL_a_cash_tender_carries_no_kind(self):
        """A cash line has no such concept; a kind on it would be noise that
        later code could branch on."""
        self._open_shift()
        create_pos_transaction(
            [{'item_id': self.milk_tea.id, 'quantity': 1}],
            'cash', cashier=self.cashier,
        )
        self.assertEqual(PaymentLine.objects.get().credit_kind, '')


class ZReportSplitTests(CreditKindTestBase):
    """The Z must not call consumption a receivable."""

    def _close(self, shift, counted=Decimal('2000.00')):
        return close_shift_and_finalize_z(shift.id, counted, self.cashier)

    def test_house_does_NOT_count_as_credit_extended(self):
        shift = self._open_shift()
        self._sell_credit(qty=8)          # 312, house by default

        z = self._close(shift)

        self.assertEqual(z.house_consumption, Decimal('312.00'))
        self.assertEqual(z.credit_extended, Decimal('0.00'))

    def test_NEGATIVE_CONTROL_a_charge_DOES_count_as_credit_extended(self):
        """Proves the assertion above is reading the kind rather than simply
        having stopped counting credit at all."""
        shift = self._open_shift()
        self._sell_credit(qty=8, kind='charge')

        z = self._close(shift)

        self.assertEqual(z.credit_extended, Decimal('312.00'))
        self.assertEqual(z.house_consumption, Decimal('0.00'))

    def test_both_kinds_in_one_shift_are_kept_apart(self):
        shift = self._open_shift()
        self._sell_credit(qty=8, kind='house', note='Clinic')
        self._sell_credit(qty=2, kind='charge', note='Regular')

        z = self._close(shift)

        self.assertEqual(z.house_consumption, Decimal('312.00'))
        self.assertEqual(z.credit_extended, Decimal('78.00'))

    def test_neither_kind_creates_a_short(self):
        """🔴 FEATURE-059's core guarantee must survive this ticket. Nothing
        was tendered either way, so a correct count still reconciles."""
        shift = self._open_shift()
        self._sell_credit(qty=8, kind='house')
        self._sell_credit(qty=2, kind='charge')

        z = self._close(shift, counted=Decimal('2000.00'))

        self.assertEqual(z.cash_expected, Decimal('2000.00'))
        self.assertEqual(z.over_short, Decimal('0.00'))

    def test_the_payment_breakdown_still_totals_all_credit(self):
        """The mix must keep reconciling to sales — the split is a breakdown,
        not a removal."""
        shift = self._open_shift()
        self._sell_credit(qty=8, kind='house')
        self._sell_credit(qty=2, kind='charge')

        z = self._close(shift)

        self.assertEqual(z.payment_breakdown.get('credit'), '390.00')
        self.assertEqual(
            z.house_consumption + z.credit_extended, Decimal('390.00')
        )


class CreditLedgerKindTests(CreditKindTestBase):

    def test_the_shift_ledger_reports_each_row_with_its_kind(self):
        shift = self._open_shift()
        self._sell_credit(qty=8, kind='house', note='Clinic')
        self._sell_credit(qty=2, kind='charge', note='Regular')

        rows = credit_lines_for_shift(shift)
        by_note = {r['note']: r for r in rows}

        self.assertEqual(by_note['Clinic']['kind'], 'house')
        self.assertEqual(by_note['Regular']['kind'], 'charge')


class ZReceiptDataTests(CreditKindTestBase):
    """What the printed Z reads to split its two headings.

    ⚠ LIMITATION, stated rather than hidden: `print_z_report` talks to
    hardware and returns early when the printer is disabled, so the ESC/POS
    rendering itself is NOT exercised by any test. What IS pinned is the data
    it reads — the per-row `kind` it filters on to itemise "Credit Extended"
    separately from "House Consumption". A pure line-builder (as
    `build_weekly_report_lines` already is for the weekly) would close this
    gap; that refactor is out of scope for this ticket.
    """

    def test_rows_are_filterable_into_the_two_headings(self):
        shift = self._open_shift()
        self._sell_credit(qty=8, kind='house', note='Clinic')
        self._sell_credit(qty=2, kind='charge', note='Regular')
        self._sell_credit(qty=1, kind='house', note="Doc's food")

        rows = credit_lines_for_shift(shift)
        house = [r for r in rows if r['kind'] == 'house']
        charge = [r for r in rows if r['kind'] == 'charge']

        self.assertEqual(len(house), 2)
        self.assertEqual(len(charge), 1)
        self.assertEqual(sum(r['amount'] for r in house), Decimal('351.00'))
        self.assertEqual(sum(r['amount'] for r in charge), Decimal('78.00'))
        # Every row keeps its own attribution — two house tabs are two rows.
        self.assertEqual({r['note'] for r in house}, {'Clinic', "Doc's food"})
