"""B-INVESTIGATE-INV (Bug A) — the items list must never truncate.

Field report (PROD) + dev repro: a newly added product "didn't register".
Root cause: the global DRF PAGE_SIZE=50 paginated /items/, and every
frontend consumer (POS product grid, inventory table, dashboard low-stock
widgets) renders only ``results`` of page 1 — so once the catalog crossed
50 items, anything past the alphabetical cutoff silently disappeared from
every screen while saving perfectly fine server-side. ItemViewSet now
disables pagination; this pins the full, un-truncated list shape.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import BusinessProfile, Item, User


class ItemListNotTruncatedTests(APITestCase):
    def setUp(self):
        BusinessProfile.objects.create(
            business_name='Test', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        Item.objects.bulk_create([
            Item(name=f'Item {n:03d}', price=Decimal('10.00'), stock=5)
            for n in range(60)
        ])
        self.client.force_authenticate(self.cashier)

    def test_list_returns_all_items_beyond_old_page_size(self):
        resp = self.client.get('/api/canteen/items/')
        self.assertEqual(resp.status_code, 200)
        # Unpaginated: a plain list, not {count, next, results}.
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 60)

    def test_newly_added_item_past_cutoff_is_in_the_list(self):
        # 'Zucchini Bread' sorts after every 'Item NNN' — the exact shape of
        # the field bug (new product alphabetically past the page-1 cutoff).
        Item.objects.create(name='Zucchini Bread', price=Decimal('50.00'), stock=3)
        resp = self.client.get('/api/canteen/items/')
        names = [row['name'] for row in resp.data]
        self.assertIn('Zucchini Bread', names)
