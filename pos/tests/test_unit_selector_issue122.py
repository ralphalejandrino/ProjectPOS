"""ISSUE-122 — the API behind the recipe unit selector.

Two endpoints:
  /ingredients/<id>/units/   what a recipe line may be entered in
  /unit-conversions/         CRUD for the extra measuring units

🔴 The property under test: the selector must offer EXACTLY what
services.convert_to_base_units will accept. If the list were re-derived in JS
it would be a second implementation of the resolution order, and the UI would
eventually offer a unit the backend rejects — which is the duplication the
FEATURE-050 reconciliation just removed.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import (
    Ingredient, IngredientUnit, IngredientUnitConversion, User,
)


class UnitsEndpointTests(APITestCase):
    def setUp(self):
        self.g = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})[0]
        self.sack = IngredientUnit.objects.get_or_create(
            abbreviation='sack', defaults={'name': 'Sack'})[0]
        self.scoop = IngredientUnit.objects.get_or_create(
            abbreviation='scoop', defaults={'name': 'Scoop'})[0]
        self.flour = Ingredient.objects.create(
            name='Flour', unit=self.g, cost_per_unit=Decimal('0.05'),
            current_stock=Decimal('50000'))
        self.admin = User.objects.create_user(
            username='a1', password='x', role='admin')
        self.client.force_authenticate(user=self.admin)

    def _units(self):
        res = self.client.get(f'/api/pos/ingredients/{self.flour.id}/units/')
        self.assertEqual(res.status_code, 200)
        return {u['abbreviation']: u for u in res.data['units']}

    def test_base_unit_is_always_offered(self):
        units = self._units()
        self.assertIn('g', units)
        self.assertEqual(units['g']['factor'], '1')
        self.assertEqual(units['g']['source'], 'base')

    def test_purchase_unit_is_offered_without_any_conversion_row(self):
        """FEATURE-050 already defines it; the selector must reuse that."""
        self.flour.purchase_unit = self.sack
        self.flour.purchase_to_base_factor = Decimal('25000')
        self.flour.save()

        units = self._units()
        self.assertEqual(units['sack']['factor'], '25000.0000')
        self.assertEqual(units['sack']['source'], 'purchase')

    def test_NEGATIVE_CONTROL_purchase_unit_absent_when_not_configured(self):
        """Proves the row above comes from the ingredient, not a constant."""
        self.assertNotIn('sack', self._units())

    def test_explicit_conversions_are_offered(self):
        IngredientUnitConversion.objects.create(
            ingredient=self.flour, unit=self.scoop,
            to_base_factor=Decimal('30'))

        units = self._units()
        self.assertEqual(units['scoop']['factor'], '30.000000')
        self.assertEqual(units['scoop']['source'], 'conversion')

    def test_offered_units_are_exactly_what_conversion_accepts(self):
        """🔴 The contract. Every offered unit must convert; nothing else may."""
        from pos.services import convert_to_base_units, UnitConversionError
        self.flour.purchase_unit = self.sack
        self.flour.purchase_to_base_factor = Decimal('25000')
        self.flour.save()
        IngredientUnitConversion.objects.create(
            ingredient=self.flour, unit=self.scoop,
            to_base_factor=Decimal('30'))

        offered = self._units()
        for abbr, u in offered.items():
            unit = IngredientUnit.objects.get(pk=u['unit'])
            got = convert_to_base_units(self.flour, Decimal('1'), unit)
            self.assertEqual(got, Decimal(u['factor']).quantize(
                Decimal('0.0001')), abbr)

        # And a unit that is NOT offered must be rejected.
        tbsp = IngredientUnit.objects.get_or_create(
            abbreviation='tbsp', defaults={'name': 'Tablespoon'})[0]
        self.assertNotIn('tbsp', offered)
        with self.assertRaises(UnitConversionError):
            convert_to_base_units(self.flour, Decimal('1'), tbsp)


class ConversionCrudTests(APITestCase):
    def setUp(self):
        self.g = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})[0]
        self.scoop = IngredientUnit.objects.get_or_create(
            abbreviation='scoop', defaults={'name': 'Scoop'})[0]
        self.sack = IngredientUnit.objects.get_or_create(
            abbreviation='sack', defaults={'name': 'Sack'})[0]
        self.matcha = Ingredient.objects.create(
            name='Matcha', unit=self.g, cost_per_unit=Decimal('2'),
            current_stock=Decimal('500'))
        self.admin = User.objects.create_user(
            username='a2', password='x', role='admin')
        self.client.force_authenticate(user=self.admin)

    URL = '/api/pos/unit-conversions/'

    def test_create_a_scoop(self):
        res = self.client.post(self.URL, {
            'ingredient': self.matcha.id, 'unit': self.scoop.id,
            'to_base_factor': '2.5'}, format='json')
        self.assertEqual(res.status_code, 201)

    def test_filter_by_ingredient(self):
        IngredientUnitConversion.objects.create(
            ingredient=self.matcha, unit=self.scoop,
            to_base_factor=Decimal('2.5'))
        other = Ingredient.objects.create(
            name='Sugar', unit=self.g, cost_per_unit=Decimal('1'),
            current_stock=Decimal('10'))

        res = self.client.get(f'{self.URL}?ingredient={other.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 0)

        res = self.client.get(f'{self.URL}?ingredient={self.matcha.id}')
        self.assertEqual(len(res.data), 1)

    def test_shadowing_the_base_unit_is_a_400_not_a_500(self):
        res = self.client.post(self.URL, {
            'ingredient': self.matcha.id, 'unit': self.g.id,
            'to_base_factor': '1'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_shadowing_the_purchase_unit_is_a_400_not_a_500(self):
        """The message must send them to where the value already lives."""
        self.matcha.purchase_unit = self.sack
        self.matcha.purchase_to_base_factor = Decimal('1000')
        self.matcha.save()

        res = self.client.post(self.URL, {
            'ingredient': self.matcha.id, 'unit': self.sack.id,
            'to_base_factor': '999'}, format='json')

        self.assertEqual(res.status_code, 400)
        self.assertIn('purchase unit', str(res.data).lower())

    def test_non_positive_factor_rejected(self):
        for bad in ('0', '-3'):
            res = self.client.post(self.URL, {
                'ingredient': self.matcha.id, 'unit': self.scoop.id,
                'to_base_factor': bad}, format='json')
            self.assertEqual(res.status_code, 400, bad)
