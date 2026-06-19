"""B8 — IngredientLog keystone coverage.

Exercises the FEATURE-009 ledger end to end:
  - sales write IngredientLog(action='sale') with before/after snapshots,
    cashier attribution, and a transaction link (ISSUE-069),
  - FLAG-046 track_depletion gate skips ingredients opted out,
  - a sale never blocks and stock is allowed to go negative (ISSUE-069),
  - voids mirror in reverse (action='void'),
  - the /adjust/ endpoint is the only direct stock path and writes
    action='adjustment' attributed to the caller (ISSUE-071),
  - PATCH can no longer write current_stock directly (ISSUE-071).
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User,
    IngredientUnit, Ingredient, RecipeIngredient,
    IngredientLog,
)
from canteen.services import create_pos_transaction, _restore_ingredients


class IngredientLogBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )
        self.START = Decimal('100.0000')
        self.ing = Ingredient.objects.create(
            name='Beans', unit=self.unit, cost_per_unit=Decimal('1.0000'),
            current_stock=self.START,
        )
        self.item = Item.objects.create(
            name='Brewed Coffee', price=Decimal('50.00'), stock=1000
        )
        RecipeIngredient.objects.create(
            item=self.item, ingredient=self.ing, quantity_used=Decimal('2.0000')
        )

    def _sell(self, qty=1):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': qty}],
            'cash', cashier=self.cashier, cash_received=Decimal('500.00'),
        )


class SaleDepletionTests(IngredientLogBase):
    def test_sale_writes_ledger_with_snapshots_and_attribution(self):
        txn = self._sell(qty=3)
        self.ing.refresh_from_db()
        # 3 units * 2.0 per unit = 6.0 depleted.
        self.assertEqual(self.ing.current_stock, self.START - Decimal('6.0000'))

        log = IngredientLog.objects.get(ingredient=self.ing, action='sale')
        self.assertEqual(log.quantity_change, Decimal('-6.0000'))
        self.assertEqual(log.stock_before, self.START)
        self.assertEqual(log.stock_after, self.START - Decimal('6.0000'))
        self.assertEqual(log.transaction_id, txn.id)
        self.assertEqual(log.performed_by_id, self.cashier.id)

    def test_track_depletion_false_skips_ledger_and_stock(self):
        self.ing.track_depletion = False
        self.ing.save(update_fields=['track_depletion'])
        self._sell(qty=2)
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, self.START)
        self.assertFalse(
            IngredientLog.objects.filter(ingredient=self.ing).exists()
        )

    def test_sale_never_blocks_and_stock_may_go_negative(self):
        self.ing.current_stock = Decimal('1.0000')
        self.ing.save(update_fields=['current_stock'])
        # 5 * 2.0 = 10.0 depletion against 1.0 on hand → -9.0, must NOT raise.
        self._sell(qty=5)
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal('-9.0000'))
        log = IngredientLog.objects.get(ingredient=self.ing, action='sale')
        self.assertEqual(log.stock_after, Decimal('-9.0000'))

    def test_void_mirrors_in_reverse(self):
        txn = self._sell(qty=1)
        self.ing.refresh_from_db()
        depleted = self.ing.current_stock
        for item_entry in txn.items.all():
            _restore_ingredients(
                item_entry.item, item_entry, item_entry.quantity,
                transaction=txn, performed_by=self.manager,
            )
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, self.START)
        vlog = IngredientLog.objects.get(ingredient=self.ing, action='void')
        self.assertEqual(vlog.quantity_change, Decimal('2.0000'))
        self.assertEqual(vlog.stock_before, depleted)
        self.assertEqual(vlog.stock_after, self.START)
        self.assertEqual(vlog.performed_by_id, self.manager.id)


class AdjustEndpointTests(IngredientLogBase):
    def test_adjust_writes_ledger_and_updates_stock(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            f'/api/canteen/ingredients/{self.ing.id}/adjust/',
            {'quantity_change': '-5.5', 'notes': 'spoilage'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, self.START - Decimal('5.5000'))
        log = IngredientLog.objects.get(ingredient=self.ing, action='adjustment')
        self.assertEqual(log.quantity_change, Decimal('-5.5000'))
        self.assertEqual(log.stock_after, self.START - Decimal('5.5000'))
        self.assertEqual(log.performed_by_id, self.manager.id)
        self.assertEqual(log.notes, 'spoilage')

    def test_adjust_rejects_zero(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            f'/api/canteen/ingredients/{self.ing.id}/adjust/',
            {'quantity_change': '0'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_adjust_new_stock_sets_absolute_value(self):
        # BUG-017: new_stock sets the absolute value; the server computes the
        # delta against the locked row, so the result is exactly what's typed
        # regardless of any stale client baseline.
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            f'/api/canteen/ingredients/{self.ing.id}/adjust/',
            {'new_stock': '960.0000', 'notes': 'opening stock'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal('960.0000'))
        log = IngredientLog.objects.get(ingredient=self.ing, action='adjustment')
        # delta = 960 - START(100) = 860
        self.assertEqual(log.quantity_change, Decimal('860.0000'))
        self.assertEqual(log.stock_before, self.START)
        self.assertEqual(log.stock_after, Decimal('960.0000'))
        self.assertEqual(log.performed_by_id, self.manager.id)

    def test_adjust_new_stock_equal_is_noop(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            f'/api/canteen/ingredients/{self.ing.id}/adjust/',
            {'new_stock': str(self.START)}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            IngredientLog.objects.filter(ingredient=self.ing).exists()
        )

    def test_adjust_rejects_new_stock_negative(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            f'/api/canteen/ingredients/{self.ing.id}/adjust/',
            {'new_stock': '-1'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_adjust_rejects_both_params(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post(
            f'/api/canteen/ingredients/{self.ing.id}/adjust/',
            {'new_stock': '5', 'quantity_change': '5'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_patch_cannot_write_current_stock(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.patch(
            f'/api/canteen/ingredients/{self.ing.id}/',
            {'current_stock': '999.0000', 'par_level': '7.0000'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.ing.refresh_from_db()
        # par_level updated, current_stock untouched (read-only on update).
        self.assertEqual(self.ing.par_level, Decimal('7.0000'))
        self.assertEqual(self.ing.current_stock, self.START)
