"""FLAG-078 — restocks now append to the IngredientLog movement ledger.

Before this fix the append-only ledger recorded sales/voids/adjustments/
corrections but NEVER restocks: the 'restock' action choice existed yet nothing
wrote it, so the ledger under-counted every replenishment (a silent cause of the
negative-stock drift seen on the PROD box). IngredientRestockLog.save() now
writes one IngredientLog(action='restock') on a genuine new purchase.

Hardening (PROD bar): positive assertions on the snapshot + attribution, plus
NEGATIVE CONTROLS proving the three correction verbs never spawn a spurious
'restock' row, that the target of a re-attribution is logged exactly once (as a
'correction', not double-logged), and that restock rows never leak into the X/Z
stock-movement section.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, Shift, User,
    IngredientUnit, Ingredient,
    IngredientLog, IngredientRestockLog,
)
from pos.services import (
    void_restock, edit_restock, reattribute_restock,
    stock_movements_for_shift,
)


class RestockLedgerBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Pos', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )
        self.START = Decimal('100.0000')
        self.ing = Ingredient.objects.create(
            name='Beans', unit=self.unit, cost_per_unit=Decimal('1.0000'),
            current_stock=self.START,
        )
        # Re-attribution target — the ingredient the purchase SHOULD have hit.
        self.other = Ingredient.objects.create(
            name='Sugar', unit=self.unit, cost_per_unit=Decimal('0.0000'),
            current_stock=Decimal('0.0000'),
        )

    def _restock(self, ing, qty, cost, user=None):
        r = IngredientRestockLog(
            ingredient=ing, quantity_added=Decimal(qty),
            cost_per_unit=Decimal(cost),
            recorded_by=user if user is not None else self.manager,
        )
        r.save()
        return r

    def _restock_rows(self, ing):
        return IngredientLog.objects.filter(ingredient=ing, action='restock')

    def _correction_rows(self, ing):
        return IngredientLog.objects.filter(ingredient=ing, action='correction')


class RestockWritesLedgerTests(RestockLedgerBase):
    def test_restock_writes_single_row_with_snapshots_and_attribution(self):
        self._restock(self.ing, '40', '2.0')

        rows = self._restock_rows(self.ing)
        self.assertEqual(rows.count(), 1, 'a restock writes exactly one ledger row')
        log = rows.get()
        # Positive quantity_change (a replenishment), before/after bracket the roll.
        self.assertEqual(log.quantity_change, Decimal('40.0000'))
        self.assertEqual(log.stock_before, self.START)
        self.assertEqual(log.stock_after, self.START + Decimal('40.0000'))
        self.assertEqual(log.performed_by_id, self.manager.id)
        # A restock is not a sale/void — no transaction linkage.
        self.assertIsNone(log.transaction_id)

    def test_snapshot_matches_actual_stock_after_roll(self):
        self._restock(self.ing, '40', '2.0')
        self.ing.refresh_from_db()
        log = self._restock_rows(self.ing).get()
        # The ledger snapshot must equal the real post-roll stock, not drift.
        self.assertEqual(self.ing.current_stock, Decimal('140.0000'))
        self.assertEqual(log.stock_after, self.ing.current_stock)

    def test_restock_via_api_attributes_recorded_by(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            f'/api/pos/ingredients/{self.ing.id}/restock/',
            {'quantity_added': '10', 'cost_per_unit': '3'}, format='json',
        )
        self.assertEqual(resp.status_code, 201)
        log = self._restock_rows(self.ing).get()
        self.assertEqual(log.quantity_change, Decimal('10.0000'))
        self.assertEqual(log.stock_before, self.START)
        self.assertEqual(log.stock_after, self.START + Decimal('10.0000'))
        self.assertEqual(log.performed_by_id, self.manager.id)

    def test_two_restocks_log_two_rows(self):
        self._restock(self.ing, '40', '2.0')
        self._restock(self.ing, '10', '2.0')
        self.assertEqual(self._restock_rows(self.ing).count(), 2)


class RestockLedgerNegativeControls(RestockLedgerBase):
    """The three FEATURE-058 correction verbs must NOT emit a 'restock' row."""

    def test_edit_restock_adds_no_new_restock_row(self):
        r = self._restock(self.ing, '40', '2.0')
        self.assertEqual(self._restock_rows(self.ing).count(), 1)

        edit_restock(
            r, quantity_added=Decimal('50'), user=self.manager, reason='miscount')

        # An edit re-uses update_fields (never the is_new branch): the restock
        # row count is unchanged, and the movement is logged as a 'correction'.
        self.assertEqual(self._restock_rows(self.ing).count(), 1)
        self.assertEqual(self._correction_rows(self.ing).count(), 1)

    def test_void_restock_logs_correction_not_restock(self):
        r = self._restock(self.ing, '40', '2.0')

        void_restock(r, user=self.manager, reason='wrong entry')

        # The void must not create a second restock row; it logs a correction.
        self.assertEqual(self._restock_rows(self.ing).count(), 1)
        self.assertEqual(self._correction_rows(self.ing).count(), 1)

    def test_reattribute_target_logged_once_as_correction_not_double(self):
        # Purchase mis-attributed to Beans; re-attribute it to Sugar.
        r = self._restock(self.ing, '40', '2.0')

        reattribute_restock(
            r, target_ingredient=self.other,
            quantity_added=Decimal('40'), cost_per_unit=Decimal('2.0'),
            user=self.manager, reason='wrong ingredient')

        # THE DOUBLE-LOG GUARD: the target's fresh restock row is created with
        # log_movement=False, so the target has exactly ONE ledger row and it is
        # a 'correction' (the re-attribution), never a duplicate 'restock'.
        tgt_rows = IngredientLog.objects.filter(ingredient=self.other)
        self.assertEqual(tgt_rows.count(), 1)
        self.assertEqual(tgt_rows.get().action, 'correction')
        self.assertFalse(self._restock_rows(self.other).exists())

    def test_reattribute_still_moves_target_stock_despite_suppressed_log(self):
        # Suppressing the ledger row must NOT suppress the stock/cost roll.
        r = self._restock(self.ing, '40', '2.0')
        reattribute_restock(
            r, target_ingredient=self.other,
            quantity_added=Decimal('40'), cost_per_unit=Decimal('2.0'),
            user=self.manager, reason='wrong ingredient')
        self.other.refresh_from_db()
        self.assertEqual(self.other.current_stock, Decimal('40.0000'))
        self.assertEqual(self.other.cost_per_unit, Decimal('2.0000'))


class RestockLedgerBreakNothing(RestockLedgerBase):
    def test_restock_rows_never_surface_in_shift_movements(self):
        # FEATURE-008 X/Z stock movements read the ledger filtered to
        # action in (sale, void) + transaction__shift. A restock row (action
        # 'restock', transaction NULL) must be doubly excluded.
        shift = Shift.objects.create(
            cashier=self.manager, opening_cash=Decimal('0.00'), is_open=True)
        self._restock(self.ing, '40', '2.0')
        self.assertEqual(stock_movements_for_shift(shift), [])
