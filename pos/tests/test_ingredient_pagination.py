"""BUG-015 — catalog lists must not be truncated by global pagination.

The project sets a global DEFAULT_PAGINATION_CLASS with PAGE_SIZE=50
(pos_config/settings.py). The inventory/ingredients frontends read only the
page-1 ``results`` array with no next-page handling, so any inherited paginator
silently hides every row past the alphabetical cutoff. A manager who saved a
new ingredient (e.g. "wintermelon") past row 50 got a success toast but never
saw it appear.

This guards the fix: the top-level catalog/reference viewsets
(Ingredient, Supplier, IngredientUnit, ItemCategory, VariantGroup) disable
pagination (pagination_class = None), matching the earlier ItemViewSet fix, so
each list endpoint returns a plain unwrapped array containing every row.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import (
    IngredientUnit, Ingredient, Supplier, User, ItemCategory, VariantGroup,
)


class CatalogPaginationTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Grams'}
        )
        # 60 ingredients > PAGE_SIZE (50). "wintermelon" sorts last, so under a
        # paginated response it would land on page 2 and disappear.
        for i in range(59):
            Ingredient.objects.create(
                name=f'ing-{i:02d}', unit=self.unit,
                cost_per_unit=Decimal('1.0000'),
            )
        Ingredient.objects.create(
            name='wintermelon', unit=self.unit, cost_per_unit=Decimal('1.0000'),
        )
        self.client.force_authenticate(self.manager)

    def _assert_full_list(self, url, expected_len):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, f'{url} -> {resp.status_code}')
        # A plain list, not a {count, next, results} pagination envelope.
        self.assertIsInstance(resp.data, list, f'{url} returned a paginated envelope')
        self.assertEqual(len(resp.data), expected_len, f'{url} truncated')
        return resp

    def test_ingredients_unpaginated_includes_last_row(self):
        resp = self._assert_full_list('/api/pos/ingredients/', 60)
        names = {row['name'] for row in resp.data}
        self.assertIn('wintermelon', names)

    def test_suppliers_unpaginated(self):
        for i in range(55):
            Supplier.objects.create(name=f'sup-{i:02d}')
        self._assert_full_list('/api/pos/suppliers/', 55)

    def test_units_unpaginated(self):
        # IngredientUnit is seeded by migration 0016, so compare against the
        # live DB count rather than a hardcoded number; the guarantee is that
        # the endpoint returns every row (no 50-row cutoff), not a fixed total.
        for i in range(55):
            IngredientUnit.objects.create(name=f'unit-{i:02d}', abbreviation=f'u{i:02d}')
        self._assert_full_list('/api/pos/ingredient-units/', IngredientUnit.objects.count())

    def test_categories_unpaginated(self):
        for i in range(55):
            ItemCategory.objects.create(name=f'cat-{i:02d}')
        self._assert_full_list('/api/pos/categories/', 55)

    def test_variant_groups_unpaginated(self):
        for i in range(55):
            VariantGroup.objects.create(name=f'grp-{i:02d}')
        self._assert_full_list('/api/pos/variant-groups/', 55)
