"""BUG-006 — deleting an item with sales history must archive, not 500.

PosTransactionItem references Item with on_delete=PROTECT so past
transactions / Z-reports can never be corrupted. Hard-deleting a sold item
used to raise an uncaught ProtectedError → HTTP 500. ItemViewSet.destroy now:

  * hard-deletes items that were never sold (→ 204), and
  * soft-deletes (is_active=False, → 200 with a detail message) any item the
    DB protects, leaving the sales history intact.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, PosTransactionItem, Shift, User,
)
from canteen.services import create_pos_transaction


class ItemSoftDeleteTests(APITestCase):
    def setUp(self):
        BusinessProfile.objects.create(
            business_name='Test', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        # destroy is gated by HasPageAccess('inventory'); admin has all pages.
        self.admin = User.objects.create_user(
            username='admin', password='x', role='admin'
        )
        # Sales require an open shift.
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0.00'), is_open=True
        )
        self.client.force_authenticate(self.admin)

    def test_delete_item_without_references_hard_deletes(self):
        item = Item.objects.create(name='Never Sold', price=Decimal('10.00'), stock=5)
        resp = self.client.delete(f'/api/canteen/items/{item.id}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Item.objects.filter(id=item.id).exists())

    def test_delete_item_with_sales_history_archives_not_500(self):
        item = Item.objects.create(name='Coffee', price=Decimal('100.00'), stock=1000)
        txn = create_pos_transaction(
            [{'item_id': item.id, 'quantity': 2}], 'cash',
            cashier=self.cashier, cash_received=Decimal('500.00'),
        )
        line = PosTransactionItem.objects.get(pos_transaction=txn, item=item)

        resp = self.client.delete(f'/api/canteen/items/{item.id}/')

        # Does not 500; reports a successful archive.
        self.assertEqual(resp.status_code, 200)
        self.assertIn('detail', resp.data)
        self.assertIn('archived', resp.data['detail'].lower())

        # Item is kept but soft-deleted (archived).
        item.refresh_from_db()
        self.assertTrue(Item.objects.filter(id=item.id).exists())
        self.assertFalse(item.is_active)

        # Sales history is intact — the protected reference still exists.
        self.assertTrue(
            PosTransactionItem.objects.filter(id=line.id).exists()
        )
