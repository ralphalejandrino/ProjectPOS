"""FEATURE-058: restock corrections — void / edit / re-attribute.

Cost recompute = quantity-weighted average of NON-VOIDED restocks; stock = delta;
every correction writes a 'correction' ledger row. Mirrors the live PROD mistake
(a ml restock mis-tapped onto a scoop ingredient).
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

from canteen.models import (
    Ingredient, IngredientUnit, IngredientRestockLog, IngredientLog, User,
)
from canteen.services import void_restock, edit_restock, reattribute_restock


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
