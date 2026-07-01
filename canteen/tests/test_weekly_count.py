"""FEATURE-055 — weekly par-based count / reconcile.

The manager enters the ABSOLUTE counted amount for flagged ingredients; the
server sets stock to that value and records variance = counted − expected as an
adjustment (negative = waste/shrinkage). Batch + atomic; zero-variance lines
write no ledger row; negative counts are rejected.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, User, IngredientUnit, Ingredient, IngredientLog,
)


class WeeklyCountTests(APITestCase):
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
        self.beans = Ingredient.objects.create(
            name='Beans', unit=self.g, cost_per_unit=Decimal('0.5'),
            current_stock=Decimal('1000'), par_level=Decimal('200'),
        )
        self.milk = Ingredient.objects.create(
            name='Milk', unit=self.g, cost_per_unit=Decimal('0.1'),
            current_stock=Decimal('500'), par_level=Decimal('100'),
        )
        self.client.force_authenticate(self.manager)

    def _reconcile(self, counts, note=None):
        body = {'counts': counts}
        if note is not None:
            body['note'] = note
        return self.client.post(
            '/api/canteen/ingredients/reconcile/', body, format='json'
        )

    def test_records_variance_as_waste_and_sets_absolute_stock(self):
        # Counted 940 vs expected 1000 → variance −60 (waste).
        resp = self._reconcile([{'id': self.beans.id, 'counted_stock': '940'}])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.beans.refresh_from_db()
        self.assertEqual(self.beans.current_stock, Decimal('940.0000'))
        row = resp.data['results'][0]
        self.assertEqual(row['variance'], -60.0)
        log = IngredientLog.objects.filter(ingredient=self.beans, action='adjustment').latest('id')
        self.assertEqual(log.quantity_change, Decimal('-60.0000'))
        self.assertEqual(log.stock_before, Decimal('1000.0000'))
        self.assertEqual(log.stock_after, Decimal('940.0000'))

    def test_batch_atomic_multiple_ingredients(self):
        resp = self._reconcile([
            {'id': self.beans.id, 'counted_stock': '950'},
            {'id': self.milk.id, 'counted_stock': '520'},  # positive variance (found stock)
        ])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.beans.refresh_from_db(); self.milk.refresh_from_db()
        self.assertEqual(self.beans.current_stock, Decimal('950.0000'))
        self.assertEqual(self.milk.current_stock, Decimal('520.0000'))

    def test_zero_variance_writes_no_ledger_row(self):
        before = IngredientLog.objects.count()
        resp = self._reconcile([{'id': self.beans.id, 'counted_stock': '1000'}])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(IngredientLog.objects.count(), before)
        self.assertEqual(resp.data['results'][0]['variance'], 0.0)

    def test_negative_count_rejected(self):
        resp = self._reconcile([{'id': self.beans.id, 'counted_stock': '-5'}])
        self.assertEqual(resp.status_code, 400)
        self.beans.refresh_from_db()
        self.assertEqual(self.beans.current_stock, Decimal('1000.0000'))

    def test_empty_counts_rejected(self):
        resp = self._reconcile([])
        self.assertEqual(resp.status_code, 400)
