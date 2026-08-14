"""FEATURE-059-FU — attribution on a credit tender (PaymentLine.note).

FEATURE-059 gave the register a way to say "sold, not paid". It did not give
it a way to say WHO did not pay. That gap is not cosmetic: the ticket's stated
owner-visible payoff was a running A/R figure, and a receivable with no name
against it is one nobody ever collects — the shop learns that ₱312 is owed and
has to reconstruct by whom from memory.

PROD's own case is the proof. Miel was tracking TWO unpaid tabs, the clinic's
drinks and separately "yung sa food ni doc", and reconciling the second one in
her head at close because the POS gave her nowhere to write it down. On
2026-08-03 that arithmetic is what went wrong: she entered ₱3,150 (the amount
she had deducted) instead of the ₱3,520 she had counted, and Z30 recorded a
₱370.00 short against a drawer that was exactly right.

Every assertion here is paired with a negative control, per the file it
extends.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import BusinessProfile, Item, PaymentLine, Shift, User
from pos.services import (
    close_shift_and_finalize_z,
    create_pos_transaction,
    credit_lines_for_shift,
)


def _bp():
    bp = BusinessProfile.get_instance()
    bp.vat_enabled = False
    bp.track_inventory = False
    bp.printer_mode = 'disabled'
    bp.business_name = 'PROD Test'
    bp.save()
    return bp


class PaymentNoteTestBase(APITestCase):
    def setUp(self):
        self.bp = _bp()
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

    def _sell_lines(self, lines, qty=8, method='credit'):
        return create_pos_transaction(
            [{'item_id': self.milk_tea.id, 'quantity': qty}],
            method, cashier=self.cashier, payment_lines=lines,
        )


class CreditNoteRoundTripTests(PaymentNoteTestBase):

    def test_note_is_stored_on_the_credit_tender(self):
        """The whole point: the ₱312 says who owes it."""
        self._open_shift()
        self._sell_lines([
            {'method': 'credit', 'amount': '312.00', 'note': 'Clinic'},
        ])

        line = PaymentLine.objects.get(method='credit')
        self.assertEqual(line.note, 'Clinic')
        self.assertEqual(line.amount, Decimal('312.00'))

    def test_NEGATIVE_CONTROL_a_sale_with_no_note_stores_empty_not_null(self):
        """Proves the assertion above is reading a real value.

        It also pins the column as non-NULL: a NULL note would make every
        downstream `.note or ''` read as a silent success while actually
        having lost the field.
        """
        self._open_shift()
        self._sell_lines([{'method': 'credit', 'amount': '312.00'}])

        line = PaymentLine.objects.get(method='credit')
        self.assertEqual(line.note, '')
        self.assertIsNotNone(line.note)

    def test_note_survives_on_a_split_where_only_part_is_credit(self):
        """A split sale must attribute ONLY its credit portion.

        This is why the note lives on the tender and not the transaction.
        """
        self._open_shift()
        self._sell_lines([
            {'method': 'cash', 'amount': '112.00'},
            {'method': 'credit', 'amount': '200.00', 'note': "Doc's food"},
        ])

        self.assertEqual(
            PaymentLine.objects.get(method='credit').note, "Doc's food"
        )
        # The cash half must not have picked the note up.
        self.assertEqual(PaymentLine.objects.get(method='cash').note, '')

    def test_note_is_truncated_never_rejected(self):
        """A long note must not be the thing that blocks a sale.

        On a live register, refusing the transaction because the attribution
        text is too long is strictly worse than shortening the text.
        """
        self._open_shift()
        long_note = 'C' * 500

        self._sell_lines([
            {'method': 'credit', 'amount': '312.00', 'note': long_note},
        ])

        line = PaymentLine.objects.get(method='credit')
        self.assertEqual(len(line.note), 200)
        self.assertTrue(line.note.startswith('CCC'))

    def test_note_is_stripped(self):
        self._open_shift()
        self._sell_lines([
            {'method': 'credit', 'amount': '312.00', 'note': '  Clinic  '},
        ])
        self.assertEqual(PaymentLine.objects.get(method='credit').note, 'Clinic')

    def test_a_plain_cash_sale_still_works_and_carries_no_note(self):
        """Backward compatibility: the no-payment_lines path is the one every
        ordinary sale at PROD takes, and it must be untouched."""
        self._open_shift()
        create_pos_transaction(
            [{'item_id': self.milk_tea.id, 'quantity': 1}],
            'cash', cashier=self.cashier,
        )

        line = PaymentLine.objects.get()
        self.assertEqual(line.method, 'cash')
        self.assertEqual(line.amount, Decimal('39.00'))
        self.assertEqual(line.note, '')


class CreditLedgerForShiftTests(PaymentNoteTestBase):
    """`credit_lines_for_shift` is what turns a bare total into an A/R list."""

    def test_itemises_each_receivable_with_its_note(self):
        shift = self._open_shift()
        self._sell_lines(
            [{'method': 'credit', 'amount': '312.00', 'note': 'Clinic'}]
        )
        self._sell_lines(
            [{'method': 'credit', 'amount': '39.00', 'note': "Doc's food"}],
            qty=1,
        )
        rows = credit_lines_for_shift(shift)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['note'], 'Clinic')
        self.assertEqual(rows[0]['amount'], Decimal('312.00'))
        self.assertEqual(rows[1]['note'], "Doc's food")
        self.assertTrue(rows[0]['transaction_no'])

    def test_two_debts_sharing_a_label_are_NOT_merged(self):
        """Aggregating by note text would hide one of two real debts."""
        shift = self._open_shift()
        for _ in range(2):
            self._sell_lines(
                [{'method': 'credit', 'amount': '312.00', 'note': 'Clinic'}]
            )

        rows = credit_lines_for_shift(shift)

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            sum(r['amount'] for r in rows), Decimal('624.00')
        )

    def test_NEGATIVE_CONTROL_cash_sales_produce_no_receivable_rows(self):
        """Proves the listing is selecting on the credit tender and is not
        just returning every payment line."""
        shift = self._open_shift()
        create_pos_transaction(
            [{'item_id': self.milk_tea.id, 'quantity': 8}],
            'cash', cashier=self.cashier,
        )

        self.assertEqual(credit_lines_for_shift(shift), [])

    def test_another_shifts_credit_does_not_leak(self):
        shift = self._open_shift()
        self._sell_lines(
            [{'method': 'credit', 'amount': '312.00', 'note': 'Clinic'}]
        )
        other = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('2000.00'), is_open=False
        )

        self.assertEqual(credit_lines_for_shift(other), [])

    def test_no_shift_is_empty_not_an_error(self):
        """The Z print path calls this with getattr(z, 'shift', None)."""
        self.assertEqual(credit_lines_for_shift(None), [])

    def test_the_note_does_not_change_the_money(self):
        """Attribution is bookkeeping, not arithmetic. Adding a note must not
        move cash_expected — that is FEATURE-059's guarantee and this ticket
        must not disturb it."""
        shift = self._open_shift()
        self._sell_lines(
            [{'method': 'credit', 'amount': '312.00', 'note': 'Clinic'}]
        )

        z = close_shift_and_finalize_z(shift.id, Decimal('2000.00'), self.cashier)

        self.assertEqual(z.cash_expected, Decimal('2000.00'))
        self.assertEqual(z.over_short, Decimal('0.00'))
        # FEATURE-065: charge + house — kind-agnostic on purpose, since this
        # test is about the NOTE not moving money, not about which bucket.
        self.assertEqual(
            z.credit_extended + z.house_consumption, Decimal('312.00')
        )


class CreditVisibleInTransactionDetailTests(PaymentNoteTestBase):
    """The note must be READABLE, not just stored.

    Ralph, looking at the demo: the transaction detail did not show that a
    sale was credit, and did not show the note. Worse than "missing" —
    `getPaymentBadge` fell back to `cash`, so an UNPAID sale was displayed as
    "💵 Cash", labelled as the exact thing this feature exists to tell it
    apart from. The API half of that fix is asserted here.
    """

    def test_api_exposes_the_credit_tender_and_its_note(self):
        from pos.serializers import PosTransactionSerializer

        self._open_shift()
        t = self._sell_lines([
            {'method': 'credit', 'amount': '312.00', 'note': 'Clinic'},
        ])

        data = PosTransactionSerializer(t).data

        self.assertEqual(data['payment_method'], 'credit')
        lines = data['payment_lines']
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['method'], 'credit')
        self.assertEqual(lines[0]['note'], 'Clinic')

    def test_NEGATIVE_CONTROL_a_cash_sale_exposes_a_cash_tender_with_no_note(self):
        """Proves the assertion above reads real per-tender data rather than
        echoing the transaction's own payment_method."""
        from pos.serializers import PosTransactionSerializer

        self._open_shift()
        t = create_pos_transaction(
            [{'item_id': self.milk_tea.id, 'quantity': 8}],
            'cash', cashier=self.cashier,
        )

        data = PosTransactionSerializer(t).data

        self.assertEqual(data['payment_method'], 'cash')
        self.assertEqual(data['payment_lines'][0]['method'], 'cash')
        self.assertEqual(data['payment_lines'][0]['note'], '')

    def test_split_exposes_both_tenders_so_the_credit_half_is_attributable(self):
        from pos.serializers import PosTransactionSerializer

        self._open_shift()
        t = self._sell_lines([
            {'method': 'cash', 'amount': '112.00'},
            {'method': 'credit', 'amount': '200.00', 'note': "Doc's food"},
        ])

        lines = PosTransactionSerializer(t).data['payment_lines']
        by_method = {l['method']: l for l in lines}

        self.assertEqual(set(by_method), {'cash', 'credit'})
        self.assertEqual(by_method['credit']['note'], "Doc's food")
        self.assertEqual(by_method['cash']['note'], '')

    def test_the_RETRIEVE_ENDPOINT_returns_the_note(self):
        """🔴 The serializer alone is not enough, and that is the whole lesson.

        `PosTransactionViewSet` is a plain ViewSet whose `retrieve` builds its
        response dict BY HAND. Adding `payment_lines` to
        PosTransactionSerializer made the in-process serializer test pass while
        the live endpoint kept returning nothing — the note was stored
        correctly, exposed correctly in one place, and still invisible in the
        UI. This test hits the endpoint the dashboard actually calls.
        """
        self._open_shift()
        t = self._sell_lines([
            {'method': 'credit', 'amount': '312.00', 'note': 'Clinic'},
        ])
        self.client.force_authenticate(user=self.cashier)

        resp = self.client.get(f'/api/pos/transactions/{t.id}/')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['payment_method'], 'credit')
        lines = resp.data['payment_lines']
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['method'], 'credit')
        self.assertEqual(lines[0]['note'], 'Clinic')

    def test_NEGATIVE_CONTROL_retrieve_on_a_cash_sale_has_no_credit_line(self):
        self._open_shift()
        t = create_pos_transaction(
            [{'item_id': self.milk_tea.id, 'quantity': 8}],
            'cash', cashier=self.cashier,
        )
        self.client.force_authenticate(user=self.cashier)

        resp = self.client.get(f'/api/pos/transactions/{t.id}/')

        self.assertEqual(resp.status_code, 200)
        methods = [l['method'] for l in resp.data['payment_lines']]
        self.assertEqual(methods, ['cash'])
        self.assertNotIn('credit', methods)
