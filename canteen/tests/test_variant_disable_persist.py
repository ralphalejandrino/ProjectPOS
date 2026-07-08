"""Regression: unchecking a category-inherited variant group on an item must
persist the disable (the PROD "Fries Flavor on the waffle" bug, 2026-07-08).

Mirrors the endpoint sequence inventory.html runs:
  load  -> GET /categories/<cat>/variant-groups/  (inheritance detection)
           GET /items/<id>/variant-groups/         (existing overrides)
  save  -> DELETE every existing override, then POST the minimal set.

The frontend's save decision (`needsRow`) is replicated here so the test pins
the exact rule. The critical property: a group that was EFFECTIVE at load and
is then unchecked always writes an enabled=false row, even if inheritance
detection is wrong at save time (paginated/incomplete category fetch, race).
"""
from decimal import Decimal
from rest_framework.test import APITestCase
from canteen.models import (
    BusinessProfile, Item, ItemCategory, User,
    VariantGroup, VariantOption, CategoryVariantGroup, ProductVariantGroup,
)


class VariantDisablePersistTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='PROD', currency='PHP', printer_mode='disabled')
        self.admin = User.objects.create_user(username='a', password='x', role='admin')
        self.client.force_authenticate(self.admin)
        self.food = ItemCategory.objects.create(name='Food')
        self.waffle = Item.objects.create(
            name='Sweet Waffle', price=Decimal('120'), stock=10, category=self.food)
        self.fries = VariantGroup.objects.create(name='Fries Flavor', is_required=True)
        VariantOption.objects.create(group=self.fries, name='Cheese')
        # Category-level assignment -> inherited by every Food item incl. the waffle.
        CategoryVariantGroup.objects.create(category=self.food, group=self.fries)

    # ---- helpers that mirror inventory.html ---------------------------------
    @staticmethod
    def _rows(resp):
        d = resp.data
        return d['results'] if isinstance(d, dict) and 'results' in d else d

    def _load(self):
        """Return the per-group load state the frontend renders."""
        cat = self._rows(self.client.get(
            f'/api/canteen/categories/{self.food.id}/variant-groups/'))
        ov = {str(o['group']['id']): o for o in self._rows(self.client.get(
            f'/api/canteen/items/{self.waffle.id}/variant-groups/'))}
        inherited_ids = {str(a['group']['id']) for a in cat}
        gid = str(self.fries.id)
        o = ov.get(gid)
        inherited = gid in inherited_ids
        enabled = (o['enabled'] is not False) if o else inherited  # load-time checkbox
        return {'gid': gid, 'inherited': inherited, 'initial_enabled': enabled}

    def _frontend_save(self, gid, inherited, initial_enabled, enabled, req=None):
        """Replicates saveProductVariantGroups: delete-all then POST minimal."""
        for a in self._rows(self.client.get(
                f'/api/canteen/items/{self.waffle.id}/variant-groups/')):
            self.client.delete(
                f'/api/canteen/items/{self.waffle.id}/variant-groups/{a["id"]}/')
        needs = (not inherited or req is not None) if enabled else (initial_enabled or inherited)
        if needs:
            self.client.post(
                f'/api/canteen/items/{self.waffle.id}/variant-groups/',
                {'group_id': gid, 'enabled': enabled, 'is_required_override': req},
                format='json')

    def _effective(self):
        r = self.client.get(f'/api/canteen/items/{self.waffle.id}/')
        return {g['group']['name'] for g in r.data['effective_variant_groups']}

    # ---- tests --------------------------------------------------------------
    def test_uncheck_inherited_group_persists_disable(self):
        st = self._load()
        self.assertTrue(st['inherited'] and st['initial_enabled'])
        self.assertIn('Fries Flavor', self._effective())
        self._frontend_save(st['gid'], st['inherited'], st['initial_enabled'], enabled=False)
        self.assertEqual(
            list(ProductVariantGroup.objects.filter(product=self.waffle)
                 .values_list('group__name', 'enabled')),
            [('Fries Flavor', False)])
        self.assertNotIn('Fries Flavor', self._effective())

    def test_disable_persists_even_if_inheritance_misdetected_at_save(self):
        # THE regression guard: group is genuinely inherited and effective, but
        # save-time inheritance detection wrongly reports inherited=False (e.g.
        # a paginated/empty category fetch). The old rule wrote no row and the
        # group came back; the initial-enabled rule still writes enabled=False.
        st = self._load()
        self._frontend_save(st['gid'], inherited=False,
                            initial_enabled=st['initial_enabled'], enabled=False)
        self.assertNotIn('Fries Flavor', self._effective())

    def test_untouched_unrelated_group_writes_no_row(self):
        # Bloat guard: a non-inherited group that was off and stays off must not
        # get a persisted row (the minimal-override property still holds).
        other = VariantGroup.objects.create(name='Add-ons')
        self._frontend_save(str(other.id), inherited=False,
                            initial_enabled=False, enabled=False)
        self.assertFalse(
            ProductVariantGroup.objects.filter(product=self.waffle, group=other).exists())
