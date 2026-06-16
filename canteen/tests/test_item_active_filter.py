"""BUG-008 — archived items: hide from the main inventory list, keep them
reachable (and restorable) via an explicit filter.

ItemViewSet.get_queryset gained opt-in active/archived filtering:
  * ?active_only=true  → live items only (inventory main list)
  * ?is_active=false   → archived items only (inventory "Archived" view)
The DEFAULT (no param) response is unchanged so reports / recipe editor /
POS / dashboard keep seeing the full catalog. Restore is a plain PATCH
is_active=true (ItemUpdateSerializer already exposes the field).
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import BusinessProfile, Item, User


class ItemActiveFilterTests(APITestCase):
    def setUp(self):
        BusinessProfile.objects.create(
            business_name='Test', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        # Inventory writes (PATCH) are gated by HasPageAccess('inventory');
        # admin has every page. Reads are cashier+.
        self.admin = User.objects.create_user(
            username='admin', password='x', role='admin'
        )
        self.client.force_authenticate(self.admin)

        self.live = Item.objects.create(
            name='Live Coffee', price=Decimal('100.00'), stock=10, is_active=True
        )
        self.archived = Item.objects.create(
            name='Archived Tea', price=Decimal('80.00'), stock=0, is_active=False
        )

    def test_active_only_returns_only_active(self):
        resp = self.client.get('/api/canteen/items/?active_only=true')
        self.assertEqual(resp.status_code, 200)
        ids = {str(row['id']) for row in resp.data}
        self.assertIn(str(self.live.id), ids)
        self.assertNotIn(str(self.archived.id), ids)

    def test_default_list_unchanged_includes_archived(self):
        resp = self.client.get('/api/canteen/items/')
        self.assertEqual(resp.status_code, 200)
        ids = {str(row['id']) for row in resp.data}
        # DEFAULT behaviour must still return the full catalog.
        self.assertIn(str(self.live.id), ids)
        self.assertIn(str(self.archived.id), ids)

    def test_is_active_false_returns_only_archived(self):
        resp = self.client.get('/api/canteen/items/?is_active=false')
        self.assertEqual(resp.status_code, 200)
        ids = {str(row['id']) for row in resp.data}
        self.assertIn(str(self.archived.id), ids)
        self.assertNotIn(str(self.live.id), ids)

    def test_restore_sets_is_active_true(self):
        resp = self.client.patch(
            f'/api/canteen/items/{self.archived.id}/',
            {'is_active': True}, format='json',
        )
        self.assertIn(resp.status_code, (200, 202))
        self.archived.refresh_from_db()
        self.assertTrue(self.archived.is_active)
        # Restored item now appears in the active-only list.
        resp = self.client.get('/api/canteen/items/?active_only=true')
        ids = {str(row['id']) for row in resp.data}
        self.assertIn(str(self.archived.id), ids)
