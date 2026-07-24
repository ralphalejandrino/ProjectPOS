"""FEATURE-051 — weighted moving-average ingredient costing.

Every restock rolls its purchase price into the ingredient's cost_per_unit:

    new_cost = (old_qty*old_cost + bought_qty*new_price) / (old_qty + bought_qty)

so COGS stays accurate as prices drift, with zero manager decisions. When prior
stock is <=0 (oversold — ISSUE-069 lets stock go negative), the old cost applies
to stock that isn't there, so the new purchase price is adopted outright.

Adjustments (count corrections) must NOT move cost — only purchases do. Editing
an existing restock log must not re-apply (stock or cost).
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, User, IngredientUnit, Ingredient,
    IngredientRestockLog,
)


class WeightedAvgBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.g, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'}
        )

    def _ingredient(self, stock, cost):
        return Ingredient.objects.create(
            name='Beans', unit=self.g,
            current_stock=Decimal(stock), cost_per_unit=Decimal(cost),
        )

    def _restock(self, ing, qty, price):
        # Model-level create triggers the save() weighted-average logic.
        return IngredientRestockLog.objects.create(
            ingredient=ing, quantity_added=Decimal(qty),
            cost_per_unit=Decimal(price),
        )


class WeightedAverageMathTests(WeightedAvgBase):
    def test_weighted_average_into_positive_stock(self):
        ing = self._ingredient('100', '2.0000')
        self._restock(ing, '100', '4.0000')
        ing.refresh_from_db()
        # (100*2 + 100*4) / 200 = 3.0
        self.assertEqual(ing.cost_per_unit, Decimal('3.0000'))
        self.assertEqual(ing.current_stock, Decimal('200.0000'))

    def test_rounds_to_four_places_half_up(self):
        ing = self._ingredient('3', '1.0000')
        self._restock(ing, '4', '2.0000')
        ing.refresh_from_db()
        # (3*1 + 4*2) / 7 = 11/7 = 1.571428... -> 1.5714
        self.assertEqual(ing.cost_per_unit, Decimal('1.5714'))

    def test_adopts_new_price_when_prior_stock_zero(self):
        ing = self._ingredient('0', '2.0000')
        self._restock(ing, '50', '5.0000')
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('5.0000'))
        self.assertEqual(ing.current_stock, Decimal('50.0000'))

    def test_adopts_new_price_when_oversold_negative(self):
        ing = self._ingredient('100', '2.0000')
        # Force negative stock the way the sale path does (bypass validators).
        Ingredient.objects.filter(pk=ing.pk).update(current_stock=Decimal('-5'))
        self._restock(ing, '100', '4.0000')
        ing.refresh_from_db()
        # old_qty <= 0 -> adopt new price, not a weighted average.
        self.assertEqual(ing.cost_per_unit, Decimal('4.0000'))
        self.assertEqual(ing.current_stock, Decimal('95.0000'))


class CostStabilityTests(WeightedAvgBase):
    def test_editing_existing_restock_does_not_reapply(self):
        ing = self._ingredient('100', '2.0000')
        log = self._restock(ing, '100', '4.0000')
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('3.0000'))
        self.assertEqual(ing.current_stock, Decimal('200.0000'))
        # Re-saving the same (existing) log must not touch stock or cost again.
        log.notes = 'edited'
        log.save()
        ing.refresh_from_db()
        self.assertEqual(ing.cost_per_unit, Decimal('3.0000'))
        self.assertEqual(ing.current_stock, Decimal('200.0000'))

    def test_stock_adjustment_does_not_change_cost(self):
        ing = self._ingredient('100', '2.0000')
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            f'/api/pos/ingredients/{ing.id}/adjust/',
            {'new_stock': '250', 'notes': 'recount'}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('250.0000'))
        # A count correction is not a purchase — cost is unchanged.
        self.assertEqual(ing.cost_per_unit, Decimal('2.0000'))


class PackageRestockRollsCostTests(WeightedAvgBase):
    def test_package_mode_restock_updates_weighted_cost(self):
        sack, _ = IngredientUnit.objects.get_or_create(
            abbreviation='sack', defaults={'name': 'Sack'}
        )
        sugar = Ingredient.objects.create(
            name='Sugar', unit=self.g, current_stock=Decimal('5000'),
            cost_per_unit=Decimal('0.0500'), purchase_unit=sack,
            purchase_to_base_factor=Decimal('25000'),
            last_purchase_price=Decimal('1250'),
        )
        self.client.force_authenticate(self.manager)
        # Buy 2 sacks @ 1300 -> 50000 g @ 0.052/g.
        resp = self.client.post(
            f'/api/pos/ingredients/{sugar.id}/restock/',
            {'packages': '2', 'package_price': '1300'}, format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        sugar.refresh_from_db()
        # (5000*0.05 + 50000*0.052) / 55000 = 2850/55000 = 0.051818... -> 0.0518
        self.assertEqual(sugar.cost_per_unit, Decimal('0.0518'))
        self.assertEqual(sugar.current_stock, Decimal('55000.0000'))
