"""FEATURE-058 calculation integrity — the double-take pass.

Verifies corrections against an INDEPENDENT ground truth: a parallel "clean
timeline" ingredient that simply never had the mistake. After a correction,
stock and cost must land exactly where the clean timeline is — not merely
"look plausible". Also pins the two safety properties around the rest of the
system: sales history (COGS snapshots) is never rewritten, and the derived
recipe cost follows the corrected ingredient cost.
"""
from decimal import Decimal

from django.test import TestCase

from canteen.models import (
    Ingredient, IngredientUnit, IngredientRestockLog, Item, ItemCategory,
    RecipeIngredient, PosTransactionItem, User,
)
from canteen.services import (
    void_restock, edit_restock, reattribute_restock, item_recipe_cost,
)


class CorrectionEqualsCleanTimelineTests(TestCase):
    """After fixing a mistake, the ingredient must equal one that never had it."""

    def setUp(self):
        self.user = User.objects.create_user(username='mgr', password='x', role='manager')
        self.ml, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Millilitre'})

    def _ing(self, name):
        return Ingredient.objects.create(
            name=name, unit=self.ml, cost_per_unit=Decimal('0'),
            current_stock=Decimal('0'))

    def _restock(self, ing, qty, price):
        return IngredientRestockLog.objects.create(
            ingredient=ing, quantity_added=Decimal(qty), cost_per_unit=Decimal(price))

    def _assert_matches_clean(self, corrected, clean):
        corrected.refresh_from_db(); clean.refresh_from_db()
        self.assertEqual(corrected.current_stock, clean.current_stock,
                         'stock must equal the never-made-the-mistake timeline')
        self.assertEqual(corrected.cost_per_unit, clean.cost_per_unit,
                         'cost must equal the never-made-the-mistake timeline')

    def test_void_restores_the_clean_timeline(self):
        messy = self._ing('Messy')
        clean = self._ing('Clean')
        # both: 100 @ 2.00, then 50 @ 3.00
        self._restock(messy, '100', '2.0'); self._restock(clean, '100', '2.0')
        self._restock(messy, '50', '3.0');  self._restock(clean, '50', '3.0')
        # only messy gets the mistake, then fixes it
        mistake = self._restock(messy, '730', '0.78')
        void_restock(mistake, user=self.user)
        self._assert_matches_clean(messy, clean)

    def test_edit_restores_the_clean_timeline(self):
        messy = self._ing('Messy')
        clean = self._ing('Clean')
        self._restock(messy, '100', '2.0'); self._restock(clean, '100', '2.0')
        # messy fat-fingers 500 @ 9.00 where clean records the true 50 @ 3.00
        typo = self._restock(messy, '500', '9.0')
        self._restock(clean, '50', '3.0')
        edit_restock(typo, quantity_added=Decimal('50'),
                     cost_per_unit=Decimal('3.0'), user=self.user)
        self._assert_matches_clean(messy, clean)

    def test_reattribute_restores_both_clean_timelines(self):
        # mistake side: tapped onto wrong_m instead of right_m
        wrong_m = self._ing('WrongMessy');  wrong_c = self._ing('WrongClean')
        right_m = self._ing('RightMessy');  right_c = self._ing('RightClean')
        for w, r in ((wrong_m, right_m), (wrong_c, right_c)):
            self._restock(w, '10', '5.0')
            self._restock(r, '200', '1.0')
        mis = self._restock(wrong_m, '730', '0.78')   # the mis-tap
        self._restock(right_c, '730', '0.78')         # clean world: entered right
        reattribute_restock(mis, target_ingredient=right_m,
                            quantity_added=Decimal('730'),
                            cost_per_unit=Decimal('0.78'), user=self.user)
        self._assert_matches_clean(wrong_m, wrong_c)
        self._assert_matches_clean(right_m, right_c)

    def test_void_only_restock_restores_pre_purchase_snapshot(self):
        """v2: voiding the ingredient's ONLY purchase restores the cost that
        existed BEFORE that purchase rolled in (cost_before snapshot) — the
        matcha case: hand-set ₱10, fat-fingered ₱500 restock rolls cost to
        ~₱300, void must bring back ₱10, not keep the polluted blend."""
        ing = Ingredient.objects.create(
            name='Matcha', unit=self.ml, cost_per_unit=Decimal('10'),
            current_stock=Decimal('0'))
        r = self._restock(ing, '100', '500')
        ing.refresh_from_db(); r.refresh_from_db()
        self.assertEqual(r.cost_before, Decimal('10.0000'))   # snapshot stamped
        self.assertEqual(ing.cost_per_unit, Decimal('500.0000'))  # adopted (stock was 0)
        void_restock(r, user=self.user)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('0'))
        self.assertEqual(ing.cost_per_unit, Decimal('10.0000'),
                         'pre-purchase cost restored from the snapshot')

    def test_void_only_restock_legacy_row_keeps_cost(self):
        """Rows recorded before the cost_before column exist with NULL snapshot
        — voiding them keeps the current cost (old behavior) rather than
        guessing or zeroing."""
        ing = self._ing('Legacy')
        r = self._restock(ing, '100', '2.0')
        IngredientRestockLog.objects.filter(pk=r.pk).update(cost_before=None)
        r.refresh_from_db()
        void_restock(r, user=self.user)
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('2.0000'),
                         'no snapshot → cost left untouched')

    def test_recost_prices_the_shelf_not_all_time(self):
        """v2: the recompute averages only the newest purchases that cover the
        CURRENT stock (FIFO — the old lot is the consumed one). Consumed cheap
        history must not drag the cost: 100@1 fully consumed + 100@2 on the
        shelf prices the shelf at 2.00, not the all-time 1.50."""
        ing = self._ing('Rice')
        self._restock(ing, '100', '1.0')
        Ingredient.objects.filter(pk=ing.pk).update(current_stock=Decimal('0'))
        self._restock(ing, '100', '2.0')
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('2.0000'))
        dup = self._restock(ing, '1', '2.0')   # tiny duplicate, then fix it
        void_restock(dup, user=self.user)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('100'))
        self.assertEqual(ing.cost_per_unit, Decimal('2.0000'),
                         'consumed 100@1 lot excluded — shelf priced at 2.00')

    def test_recost_weights_partial_oldest_lot(self):
        """The oldest lot still partly on the shelf is weighted only by the
        portion remaining. Timeline: buy 100@2 → sell down to 10 → buy 100@4 →
        shelf is 110 = 100@4 + 10 of the @2 lot → (100*4 + 10*2)/110 = 3.8182,
        which matches the live FEATURE-051 rolling value — the correction basis
        now agrees with the rolling basis instead of diverging."""
        ing = self._ing('PathDependent')
        self._restock(ing, '100', '2.0')
        Ingredient.objects.filter(pk=ing.pk).update(current_stock=Decimal('10'))
        self._restock(ing, '100', '4.0')
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('3.8182'))   # rolling (live)
        dup = self._restock(ing, '1', '4.0')
        void_restock(dup, user=self.user)
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('3.8182'),
                         'shelf-replacement recompute agrees with the rolling basis')


class SalesHistoryImmutableTests(TestCase):
    """A correction must never rewrite recorded sales — COGS snapshots on
    PosTransactionItem are frozen at sale time by design (#10 fixes coverage
    HONESTY, not history)."""

    def setUp(self):
        self.user = User.objects.create_user(username='mgr', password='x', role='manager')
        self.ml, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Millilitre'})
        self.cat = ItemCategory.objects.create(name='Drinks')

    def test_correction_does_not_touch_sale_snapshots(self):
        ing = Ingredient.objects.create(
            name='Syrup', unit=self.ml, cost_per_unit=Decimal('0'),
            current_stock=Decimal('0'))
        r = IngredientRestockLog.objects.create(
            ingredient=ing, quantity_added=Decimal('100'),
            cost_per_unit=Decimal('2.0'))
        item = Item.objects.create(
            category=self.cat, name='Latte', price=Decimal('100'), stock=0)
        RecipeIngredient.objects.create(
            item=item, ingredient=ing, quantity_used=Decimal('10'))

        # a recorded sale line with the cost snapshotted at sale time
        snapshot_cost = item_recipe_cost(item)
        self.assertEqual(snapshot_cost, Decimal('20.0000'))  # 10 * 2.0
        from canteen.models import PosTransaction
        txn = PosTransaction.objects.create(
            total_amount=Decimal('100'), payment_method='cash')
        line = PosTransactionItem.objects.create(
            pos_transaction=txn, item=item, quantity=1,
            unit_price=Decimal('100'), subtotal=Decimal('100'),
            unit_cost=snapshot_cost)

        # correct the restock price 2.0 → 5.0 afterwards
        edit_restock(r, cost_per_unit=Decimal('5.0'), user=self.user)

        # the PAST sale keeps its snapshot; the FUTURE derived cost moves
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('20.0000'),
                         'recorded sale snapshot must not be rewritten')
        self.assertEqual(item_recipe_cost(item), Decimal('50.0000'),
                         'derived recipe cost follows the corrected price')
