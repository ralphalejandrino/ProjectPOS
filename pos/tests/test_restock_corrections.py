"""FEATURE-058: restock corrections — void / edit / re-attribute.

Cost recompute = quantity-weighted average of NON-VOIDED restocks; stock = delta;
every correction writes a 'correction' ledger row. Mirrors the live PROD mistake
(a ml restock mis-tapped onto a scoop ingredient).
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

from pos.models import (
    Ingredient, IngredientUnit, IngredientRestockLog, IngredientLog, User,
)
from pos.services import void_restock, edit_restock, reattribute_restock


class RestockCorrectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='mgr', password='x', role='manager')
        self.ml, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Millilitre'})
        self.scoop, _ = IngredientUnit.objects.get_or_create(
            abbreviation='scoop', defaults={'name': 'Scoop'})

    def _ing(self, name, unit):
        return Ingredient.objects.create(
            name=name, unit=unit, cost_per_unit=Decimal('0'),
            current_stock=Decimal('0'))

    def _restock(self, ing, qty, price):
        # goes through FEATURE-051 (updates stock + rolling cost)
        return IngredientRestockLog.objects.create(
            ingredient=ing, quantity_added=Decimal(qty), cost_per_unit=Decimal(price))

    # -- void ----------------------------------------------------------------
    def test_void_removes_stock_and_recosts_from_remaining(self):
        ing = self._ing('Sugar', self.ml)
        self._restock(ing, '100', '2.0')          # stock 100, cost 2.0
        r2 = self._restock(ing, '100', '4.0')     # stock 200, cost 3.0 (rolling)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('200'))

        res = void_restock(r2, user=self.user, reason='double entry')
        ing.refresh_from_db(); r2.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('100'))       # -100
        self.assertEqual(ing.cost_per_unit, Decimal('2.0000'))    # only the 2.0 restock left
        self.assertTrue(r2.is_voided)
        self.assertEqual(r2.voided_by, self.user)
        self.assertFalse(res['negative_stock'])
        log = IngredientLog.objects.filter(ingredient=ing, action='correction').latest('id')
        self.assertEqual(log.quantity_change, Decimal('-100'))
        self.assertEqual(log.stock_after, Decimal('100'))

    def test_void_twice_is_rejected(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        void_restock(r, user=self.user)
        r.refresh_from_db()
        with self.assertRaises(DRFValidationError):
            void_restock(r, user=self.user)

    def test_void_can_go_negative_with_warning(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')       # stock 100
        # simulate the stock already sold down below the restock qty
        ing.current_stock = Decimal('30'); ing.save(update_fields=['current_stock'])
        res = void_restock(r, user=self.user)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('-70'))
        self.assertTrue(res['negative_stock'])

    # -- edit ----------------------------------------------------------------
    def test_edit_qty_and_price_adjusts_stock_and_cost(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')       # stock 100, cost 2.0
        edit_restock(r, quantity_added=Decimal('150'), cost_per_unit=Decimal('3.0'),
                     user=self.user, reason='miscount')
        ing.refresh_from_db(); r.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('150'))       # +50
        self.assertEqual(ing.cost_per_unit, Decimal('3.0000'))
        self.assertEqual(r.corrected_by, self.user)

    def test_edit_voided_is_rejected(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        void_restock(r, user=self.user); r.refresh_from_db()
        with self.assertRaises(DRFValidationError):
            edit_restock(r, quantity_added=Decimal('10'), user=self.user)

    def test_edit_nothing_is_rejected(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        with self.assertRaises(DRFValidationError):
            edit_restock(r, user=self.user)

    # -- re-attribute (the PROD mistake: ml mis-tapped onto a scoop ingredient) --
    def test_reattribute_moves_purchase_across_units(self):
        caramel = self._ing('Caramel syrup', self.ml)     # correct target (ml)
        choco = self._ing('Choco mousse', self.scoop)     # wrong, tapped here
        wrong = self._restock(choco, '730', '0.78')       # 730 scoop @ 0.78 on choco
        choco.refresh_from_db()
        self.assertEqual(choco.current_stock, Decimal('730'))

        out = reattribute_restock(
            wrong, target_ingredient=caramel,
            quantity_added=Decimal('730'), cost_per_unit=Decimal('0.2877'),
            user=self.user, reason='meant Caramel')

        wrong.refresh_from_db(); choco.refresh_from_db(); caramel.refresh_from_db()
        self.assertTrue(wrong.is_voided)
        self.assertEqual(choco.current_stock, Decimal('0'))          # removed from wrong
        self.assertEqual(caramel.current_stock, Decimal('730'))      # landed on correct
        self.assertEqual(caramel.cost_per_unit, Decimal('0.2877'))   # target-unit price
        self.assertEqual(out['new_restock'].ingredient_id, caramel.id)
        # both sides have a correction ledger row
        self.assertEqual(
            IngredientLog.objects.filter(action='correction', ingredient=choco).count(), 1)
        self.assertEqual(
            IngredientLog.objects.filter(action='correction', ingredient=caramel).count(), 1)

    def test_reattribute_same_ingredient_is_rejected(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        with self.assertRaises(DRFValidationError):
            reattribute_restock(r, target_ingredient=ing,
                                quantity_added=Decimal('100'),
                                cost_per_unit=Decimal('2.0'), user=self.user)

    # -- review findings (2026-07-19 double-take) ----------------------------

    def test_stale_instance_cannot_double_apply(self):
        """Race reproduction: two requests load the same restock, the first
        voids it, the second still holds a stale instance with
        is_voided=False. The in-transaction re-fetch must reject the loser —
        pre-fix, this deducted the restock quantity TWICE."""
        ing = self._ing('Sugar', self.ml)
        r_a = self._restock(ing, '100', '2.0')
        r_b = IngredientRestockLog.objects.get(pk=r_a.pk)   # second request's copy
        void_restock(r_a, user=self.user)
        self.assertFalse(r_b.is_voided)   # stale in memory, exactly like the race
        with self.assertRaises(DRFValidationError):
            void_restock(r_b, user=self.user)
        with self.assertRaises(DRFValidationError):
            edit_restock(r_b, quantity_added=Decimal('50'), user=self.user)
        other = self._ing('Salt', self.ml)
        with self.assertRaises(DRFValidationError):
            reattribute_restock(r_b, target_ingredient=other,
                                quantity_added=Decimal('100'),
                                cost_per_unit=Decimal('2.0'), user=self.user)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('0'),
                         'stock deducted exactly once despite the stale copy')
        self.assertEqual(IngredientLog.objects.filter(
            ingredient=ing, action='correction').count(), 1)

    def test_reattribute_preserves_purchase_date(self):
        """The replacement entry keeps the ORIGINAL purchase date — otherwise
        the weekly restock-spend report loses the purchase from its real week
        and invents spend in the correction week."""
        from datetime import timedelta
        from django.utils import timezone as dj_tz
        wrong = self._ing('Choco mousse', self.scoop)
        right = self._ing('Caramel syrup', self.ml)
        r = self._restock(wrong, '730', '0.78')
        old_date = dj_tz.now() - timedelta(days=9)
        IngredientRestockLog.objects.filter(pk=r.pk).update(date=old_date)
        r.refresh_from_db()
        out = reattribute_restock(
            r, target_ingredient=right, quantity_added=Decimal('730'),
            cost_per_unit=Decimal('0.2877'), user=self.user)
        self.assertEqual(out['new_restock'].date, old_date)

    def test_void_clears_last_purchase_price_memory(self):
        """FEATURE-050 prefills the next package restock from
        last_purchase_price; voiding the LATEST purchase clears that memory so
        a corrected fat-fingered price can't silently resurrect. CONTROL: with
        a newer valid purchase on file, the memory is kept."""
        ing = self._ing('Flour', self.ml)
        ing.last_purchase_price = Decimal('12500')   # the typo'd sack price
        ing.save(update_fields=['last_purchase_price'])
        r = self._restock(ing, '50', '250')          # the latest (only) restock
        void_restock(r, user=self.user)
        ing.refresh_from_db()
        self.assertIsNone(ing.last_purchase_price)

        # control: voiding an OLDER entry keeps the memory
        ing2 = self._ing('Rice', self.ml)
        ing2.last_purchase_price = Decimal('1250')
        ing2.save(update_fields=['last_purchase_price'])
        from datetime import timedelta
        from django.utils import timezone as dj_tz
        older = self._restock(ing2, '50', '25')
        IngredientRestockLog.objects.filter(pk=older.pk).update(
            date=dj_tz.now() - timedelta(days=5))
        older.refresh_from_db()
        self._restock(ing2, '50', '25')              # newer valid purchase
        void_restock(older, user=self.user)
        ing2.refresh_from_db()
        self.assertEqual(ing2.last_purchase_price, Decimal('1250'))

    def test_noop_edit_rejected_and_not_stamped(self):
        """Re-sending the stored values (a replayed POST) is not an edit: no
        recompute, no false 'corrected by' stamp."""
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        with self.assertRaises(DRFValidationError):
            edit_restock(r, quantity_added=Decimal('100'),
                         cost_per_unit=Decimal('2.0'), user=self.user)
        r.refresh_from_db()
        self.assertIsNone(r.corrected_by)
        self.assertIsNone(r.corrected_at)

    def test_ledger_note_has_no_dangling_parens_without_reason(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        void_restock(r, user=self.user, reason='')
        log = IngredientLog.objects.filter(
            ingredient=ing, action='correction').latest('id')
        self.assertNotIn('()', log.notes)
        self.assertEqual(log.notes, f'void restock #{r.pk}')

    # -- preparations: v1 scope guard ----------------------------------------
    def test_corrections_rejected_on_preparation_restocks(self):
        """A prep's cost is blended from batch production (no restock rows), so
        _recost from restocks alone would erase it — all three verbs refuse.
        CONTROL below proves the same calls succeed on a normal ingredient."""
        prep = Ingredient.objects.create(
            name='Simple Syrup', unit=self.ml, cost_per_unit=Decimal('1.5'),
            current_stock=Decimal('500'), is_preparation=True,
            batch_yield=Decimal('500'))
        normal = self._ing('Sugar', self.ml)
        pr = self._restock(prep, '100', '2.0')
        prep.refresh_from_db()
        stock_before = prep.current_stock
        cost_before = prep.cost_per_unit

        with self.assertRaises(DRFValidationError):
            void_restock(pr, user=self.user)
        with self.assertRaises(DRFValidationError):
            edit_restock(pr, quantity_added=Decimal('50'), user=self.user)
        with self.assertRaises(DRFValidationError):
            reattribute_restock(pr, target_ingredient=normal,
                                quantity_added=Decimal('100'),
                                cost_per_unit=Decimal('2.0'), user=self.user)
        # prep untouched by the rejected attempts
        prep.refresh_from_db(); pr.refresh_from_db()
        self.assertEqual(prep.current_stock, stock_before)
        self.assertEqual(prep.cost_per_unit, cost_before)
        self.assertFalse(pr.is_voided)

        # a prep TARGET is refused too
        nr = self._restock(normal, '100', '2.0')
        with self.assertRaises(DRFValidationError):
            reattribute_restock(nr, target_ingredient=prep,
                                quantity_added=Decimal('100'),
                                cost_per_unit=Decimal('2.0'), user=self.user)

        # CONTROL: identical void on the normal ingredient succeeds
        void_restock(nr, user=self.user)
        nr.refresh_from_db()
        self.assertTrue(nr.is_voided)
