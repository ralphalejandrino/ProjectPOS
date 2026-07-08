"""ISSUE-070 + FLAG-049 — variant recipe authoring unlock + resolution dedup.

ISSUE-070: the recipe builder's variant selector calls /items/<id>/variants/.
That endpoint did not exist, so the selector was permanently empty and
variant-specific recipes could never be authored. These tests pin the new
endpoint.

FLAG-049: effective-group resolution is now a single canonical function shared
by the sale path and the product serializer; the endpoint exercises it.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, ItemCategory, User,
    VariantGroup, VariantOption, CategoryVariantGroup, ProductVariantGroup,
)


class ItemVariantsEndpointTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Test Canteen', currency='PHP', printer_mode='disabled',
        )
        # 'ingredients' page defaults to manager/admin.
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.client.force_authenticate(self.manager)

        self.cat = ItemCategory.objects.create(name='Drinks')
        self.item = Item.objects.create(
            name='Milk Tea', price=Decimal('100.00'), stock=100, category=self.cat
        )
        # Two groups assigned to the category, each with options. Both options
        # named "Large" to prove the group prefix keeps them distinct.
        self.size = VariantGroup.objects.create(name='Size', sort_order=0)
        self.opt_size = VariantOption.objects.create(
            group=self.size, name='Large', price_modifier=Decimal('20.00')
        )
        self.ice = VariantGroup.objects.create(name='Ice', sort_order=1)
        self.opt_ice = VariantOption.objects.create(
            group=self.ice, name='Large', price_modifier=Decimal('0.00')
        )
        CategoryVariantGroup.objects.create(category=self.cat, group=self.size)
        CategoryVariantGroup.objects.create(category=self.cat, group=self.ice)

    def test_variants_endpoint_returns_group_prefixed_options(self):
        resp = self.client.get(f'/api/canteen/items/{self.item.id}/variants/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {o['name'] for o in resp.data}
        self.assertEqual(names, {'Size — Large', 'Ice — Large'})
        ids = {o['id'] for o in resp.data}
        self.assertEqual(ids, {str(self.opt_size.id), str(self.opt_ice.id)})

    def test_product_override_disables_group(self):
        # Disabling Ice at product level drops it from the effective options.
        ProductVariantGroup.objects.create(
            product=self.item, group=self.ice, enabled=False
        )
        resp = self.client.get(f'/api/canteen/items/{self.item.id}/variants/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {o['name'] for o in resp.data}
        self.assertEqual(names, {'Size — Large'})

    def test_inactive_option_excluded(self):
        self.opt_ice.is_active = False
        self.opt_ice.save()
        resp = self.client.get(f'/api/canteen/items/{self.item.id}/variants/')
        names = {o['name'] for o in resp.data}
        self.assertEqual(names, {'Size — Large'})

    def test_serializer_and_endpoint_share_resolution(self):
        # FLAG-049: the product serializer's effective_variant_groups and the
        # endpoint resolve the same effective group set.
        resp = self.client.get(f'/api/canteen/items/{self.item.id}/')
        groups = {g['group']['name'] for g in resp.data['effective_variant_groups']}
        self.assertEqual(groups, {'Size', 'Ice'})

    def test_disabling_inherited_group_on_one_item_does_not_affect_siblings(self):
        # PROD regression: a variant group assigned to a whole category (e.g.
        # "Fries Flavor" on Food) is inherited by every item. Disabling it on ONE
        # item (a waffle) via a ProductVariantGroup(enabled=False) override must
        # drop it from that item ONLY — its category siblings (the fries) keep it.
        from canteen.services import resolve_effective_variant_groups
        sibling = Item.objects.create(
            name='Fries', price=Decimal('50.00'), stock=100, category=self.cat
        )
        ProductVariantGroup.objects.create(
            product=self.item, group=self.ice, enabled=False
        )
        item_groups = {r['group'].name for r in resolve_effective_variant_groups(self.item)}
        sib_groups = {r['group'].name for r in resolve_effective_variant_groups(sibling)}
        self.assertEqual(item_groups, {'Size'})           # disabled group gone here
        self.assertEqual(sib_groups, {'Size', 'Ice'})     # sibling untouched
