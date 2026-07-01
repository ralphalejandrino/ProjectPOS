"""FEATURE-050 — ingredient purchasing-unit layer.

PROD's manager buys ingredients in packages (a sack of sugar, a box of cups) but
recipes consume base units (grams, pieces). She should never divide a sack into
grams by hand. These tests pin the package-based restock entry:

  - "N packages @ ₱P" converts to base units + per-base-unit cost via the
    ingredient's purchase_to_base_factor,
  - the most recent package price is remembered for prefill,
  - package mode is rejected when no conversion / no price is available,
  - the legacy base-unit restock path is untouched (backward compatible),
  - the ingredient serializer exposes the new purchase fields.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, User, IngredientUnit, Ingredient,
)


class PurchaseUnitRestockBase(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.gram, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'}
        )
        self.sack, _ = IngredientUnit.objects.get_or_create(
            abbreviation='sack', defaults={'name': 'Sack'}
        )
        # 1 sack = 25,000 g; last bought at ₱1,250/sack (₱0.05/g).
        self.sugar = Ingredient.objects.create(
            name='White Sugar', unit=self.gram,
            cost_per_unit=Decimal('0.0500'),
            current_stock=Decimal('5000.0000'),
            purchase_unit=self.sack,
            purchase_to_base_factor=Decimal('25000.0000'),
            last_purchase_price=Decimal('1250.00'),
        )
        # An ingredient with NO purchase unit configured.
        self.milk = Ingredient.objects.create(
            name='Fresh Milk', unit=self.gram,
            cost_per_unit=Decimal('0.1000'),
            current_stock=Decimal('1000.0000'),
        )
        self.client.force_authenticate(self.manager)

    def _restock(self, ingredient, **body):
        return self.client.post(
            f'/api/canteen/ingredients/{ingredient.id}/restock/',
            body, format='json',
        )


class PackageModeConversionTests(PurchaseUnitRestockBase):
    def test_packages_convert_to_base_units(self):
        resp = self._restock(self.sugar, packages='2', package_price='1300.00')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.sugar.refresh_from_db()
        # 5000 + 2 * 25000 = 55000 g
        self.assertEqual(self.sugar.current_stock, Decimal('55000.0000'))

    def test_package_price_becomes_per_base_unit_cost_on_log(self):
        resp = self._restock(self.sugar, packages='1', package_price='1300.00')
        self.assertEqual(resp.status_code, 201, resp.data)
        # 1300 / 25000 = 0.052
        self.assertEqual(Decimal(resp.data['cost_per_unit']), Decimal('0.0520'))
        self.assertEqual(Decimal(resp.data['quantity_added']), Decimal('25000.0000'))

    def test_package_price_is_remembered_for_prefill(self):
        self._restock(self.sugar, packages='1', package_price='1300.00')
        self.sugar.refresh_from_db()
        self.assertEqual(self.sugar.last_purchase_price, Decimal('1300.00'))

    def test_omitted_package_price_reuses_last_purchase_price(self):
        resp = self._restock(self.sugar, packages='1')
        self.assertEqual(resp.status_code, 201, resp.data)
        # Falls back to last_purchase_price 1250 / 25000 = 0.05
        self.assertEqual(Decimal(resp.data['cost_per_unit']), Decimal('0.0500'))


class PackageModeRejectionTests(PurchaseUnitRestockBase):
    def test_rejected_when_no_conversion_configured(self):
        resp = self._restock(self.milk, packages='2', package_price='500.00')
        self.assertEqual(resp.status_code, 400)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.current_stock, Decimal('1000.0000'))

    def test_rejected_when_no_price_available(self):
        self.sugar.last_purchase_price = None
        self.sugar.save(update_fields=['last_purchase_price'])
        resp = self._restock(self.sugar, packages='1')
        self.assertEqual(resp.status_code, 400)
        self.sugar.refresh_from_db()
        self.assertEqual(self.sugar.current_stock, Decimal('5000.0000'))

    def test_rejects_zero_packages(self):
        resp = self._restock(self.sugar, packages='0', package_price='1250.00')
        self.assertEqual(resp.status_code, 400)


class LegacyBaseUnitPathTests(PurchaseUnitRestockBase):
    def test_base_unit_restock_still_works(self):
        resp = self._restock(self.milk, quantity_added='500.0000', cost_per_unit='0.12')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.current_stock, Decimal('1500.0000'))

    def test_base_unit_cost_defaults_to_ingredient_cost(self):
        resp = self._restock(self.milk, quantity_added='500.0000')
        self.assertEqual(resp.status_code, 201, resp.data)
        # Defaults to the ingredient's current cost_per_unit (0.10).
        self.assertEqual(Decimal(resp.data['cost_per_unit']), Decimal('0.1000'))

    def test_neither_mode_provided_is_rejected(self):
        resp = self._restock(self.milk, notes='oops')
        self.assertEqual(resp.status_code, 400)


class PurchaseFieldsSerializedTests(PurchaseUnitRestockBase):
    def test_ingredient_exposes_purchase_fields(self):
        resp = self.client.get(f'/api/canteen/ingredients/{self.sugar.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['purchase_unit_detail']['abbreviation'], 'sack')
        self.assertEqual(
            Decimal(resp.data['purchase_to_base_factor']), Decimal('25000.0000')
        )
        self.assertEqual(
            Decimal(resp.data['last_purchase_price']), Decimal('1250.00')
        )
