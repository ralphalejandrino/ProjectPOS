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

    def test_void_only_restock_leaves_cost_untouched(self):
        """No surviving purchase to derive a cost from → the existing cost must
        NOT be zeroed (it may be a legitimate hand-set value)."""
        ing = self._ing('Lonely')
        r = self._restock(ing, '100', '2.0')
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('2.0000'))
        void_restock(r, user=self.user)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('0'))
        self.assertEqual(ing.cost_per_unit, Decimal('2.0000'),
                         'cost preserved when no purchases remain')

    def test_known_divergence_from_rolling_average_is_pinned(self):
        """DOCUMENTED trade-off: with sales between restocks, the FEATURE-051
        rolling average weights by stock-on-hand, but the correction recompute
        weights by purchase quantities. This test pins the divergence so it is
        an explicit, understood property — not an accident.

        Timeline: buy 100@2 → sell down to 10 → buy 100@4.
        Rolling: (10*2 + 100*4)/110 = 3.8182. Purchase-avg: (100*2+100*4)/200 = 3.0.
        """
        ing = self._ing('PathDependent')
        self._restock(ing, '100', '2.0')
        Ingredient.objects.filter(pk=ing.pk).update(current_stock=Decimal('10'))
        r2 = self._restock(ing, '100', '4.0')
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('3.8182'))   # rolling (live)
        # a no-op-sized edit (price unchanged, qty unchanged is rejected, so
        # edit the price to the same value via a real field change path: bump
        # then restore is overkill — edit price to same value is allowed)
        edit_restock(r2, cost_per_unit=Decimal('4.0'), user=self.user)
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('3.0000'),
                         'correction resets to the purchase-weighted average')


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
