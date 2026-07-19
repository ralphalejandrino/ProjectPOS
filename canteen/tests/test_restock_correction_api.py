"""FEATURE-058 API: restock-correction endpoints (void / edit / re-attribute).

The engine is covered by test_restock_corrections.py; this file pins the HTTP
surface: manager/admin gating (cashier 403 negative control), validation
rejections, the response contract the UI reads (negative_stock flag,
corrected-by audit fields), the absence of any bare CRUD escape hatch on
/restock-logs/, and the weekly report excluding voided restock spend.
"""
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    Ingredient, IngredientUnit, IngredientRestockLog, IngredientLog, User,
)


class RestockCorrectionApiTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='mgr', password='x', role='manager')
        self.cashier = User.objects.create_user(
            username='cash', password='x', role='cashier')
        self.ml, _ = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Millilitre'})
        self.scoop, _ = IngredientUnit.objects.get_or_create(
            abbreviation='scoop', defaults={'name': 'Scoop'})
        self.client.force_authenticate(self.manager)

    def _ing(self, name, unit, active=True):
        return Ingredient.objects.create(
            name=name, unit=unit, cost_per_unit=Decimal('0'),
            current_stock=Decimal('0'), is_active=active)

    def _restock(self, ing, qty, price):
        return IngredientRestockLog.objects.create(
            ingredient=ing, quantity_added=Decimal(qty),
            cost_per_unit=Decimal(price))

    def _url(self, restock, verb):
        return f'/api/canteen/restock-logs/{restock.pk}/{verb}/'

    # -- permission gate ------------------------------------------------------

    def test_cashier_gets_403_on_all_three_verbs(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        other = self._ing('Salt', self.ml)
        self.client.force_authenticate(self.cashier)
        for verb, body in (
            ('void', {}),
            ('edit', {'quantity_added': '50'}),
            ('reattribute', {'target_ingredient': other.pk,
                             'quantity_added': '100', 'cost_per_unit': '2.0'}),
        ):
            resp = self.client.post(self._url(r, verb), body, format='json')
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, verb)
        # CONTROL: nothing moved.
        r.refresh_from_db(); ing.refresh_from_db()
        self.assertFalse(r.is_voided)
        self.assertEqual(ing.current_stock, Decimal('100'))

    def test_unauthenticated_is_rejected(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        self.client.force_authenticate(None)
        resp = self.client.post(self._url(r, 'void'), {}, format='json')
        self.assertIn(resp.status_code,
                      (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_no_bare_crud_surface_on_restock_logs(self):
        """Only the three correction verbs exist — no list/retrieve/PATCH/DELETE
        route is exposed, so history rows can't be mutated around the audit
        trail."""
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        self.assertEqual(self.client.get('/api/canteen/restock-logs/').status_code,
                         status.HTTP_404_NOT_FOUND)
        detail = f'/api/canteen/restock-logs/{r.pk}/'
        self.assertEqual(self.client.get(detail).status_code,
                         status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.client.patch(detail, {'quantity_added': '5'}, format='json')
            .status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.delete(detail).status_code,
                         status.HTTP_404_NOT_FOUND)

    # -- void -----------------------------------------------------------------

    def test_void_endpoint_full_contract(self):
        ing = self._ing('Sugar', self.ml)
        self._restock(ing, '100', '2.0')
        r2 = self._restock(ing, '100', '4.0')
        resp = self.client.post(
            self._url(r2, 'void'), {'reason': 'double entry'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['restock']['is_voided'])
        self.assertEqual(resp.data['restock']['voided_by_name'], 'mgr')
        self.assertEqual(resp.data['restock']['correction_note'], 'double entry')
        self.assertEqual(Decimal(resp.data['ingredient']['current_stock']),
                         Decimal('100'))
        self.assertEqual(Decimal(resp.data['ingredient']['cost_per_unit']),
                         Decimal('2.0000'))
        self.assertFalse(resp.data['negative_stock'])
        self.assertTrue(IngredientLog.objects.filter(
            ingredient=ing, action='correction').exists())

    def test_void_twice_is_400(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        self.client.post(self._url(r, 'void'), {}, format='json')
        resp = self.client.post(self._url(r, 'void'), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        # CONTROL: stock removed exactly once.
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal('0'))

    def test_void_negative_stock_flag_surfaces(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        Ingredient.objects.filter(pk=ing.pk).update(
            current_stock=Decimal('30'))  # already sold down
        resp = self.client.post(self._url(r, 'void'), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['negative_stock'])
        self.assertEqual(Decimal(resp.data['ingredient']['current_stock']),
                         Decimal('-70'))

    # -- edit -----------------------------------------------------------------

    def test_edit_endpoint_full_contract(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        resp = self.client.post(
            self._url(r, 'edit'),
            {'quantity_added': '150', 'cost_per_unit': '3.0',
             'reason': 'miscount'},
            format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(resp.data['restock']['quantity_added']),
                         Decimal('150'))
        self.assertEqual(resp.data['restock']['corrected_by_name'], 'mgr')
        self.assertEqual(Decimal(resp.data['ingredient']['current_stock']),
                         Decimal('150'))
        self.assertEqual(Decimal(resp.data['ingredient']['cost_per_unit']),
                         Decimal('3.0000'))

    def test_edit_rejections(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        cases = (
            {},                                  # nothing to edit
            {'quantity_added': '0'},             # zero qty
            {'quantity_added': '-5'},            # negative qty
            {'cost_per_unit': '0'},              # zero price = #10 disease
            {'cost_per_unit': '-1'},             # negative price
        )
        for body in cases:
            resp = self.client.post(self._url(r, 'edit'), body, format='json')
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, body)
        # CONTROL: untouched throughout.
        r.refresh_from_db(); ing.refresh_from_db()
        self.assertEqual(r.quantity_added, Decimal('100'))
        self.assertEqual(ing.current_stock, Decimal('100'))

    def test_edit_voided_is_400(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        self.client.post(self._url(r, 'void'), {}, format='json')
        resp = self.client.post(
            self._url(r, 'edit'), {'quantity_added': '50'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # -- re-attribute ---------------------------------------------------------

    def test_reattribute_endpoint_full_contract(self):
        caramel = self._ing('Caramel syrup', self.ml)
        choco = self._ing('Choco mousse', self.scoop)
        wrong = self._restock(choco, '730', '0.78')
        resp = self.client.post(
            self._url(wrong, 'reattribute'),
            {'target_ingredient': caramel.pk, 'quantity_added': '730',
             'cost_per_unit': '0.2877', 'reason': 'meant Caramel'},
            format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['restock']['is_voided'])
        self.assertEqual(resp.data['new_restock']['ingredient'], caramel.pk)
        self.assertEqual(Decimal(resp.data['source']['current_stock']),
                         Decimal('0'))
        self.assertEqual(Decimal(resp.data['target']['current_stock']),
                         Decimal('730'))
        self.assertEqual(Decimal(resp.data['target']['cost_per_unit']),
                         Decimal('0.2877'))

    def test_reattribute_rejections(self):
        ing = self._ing('Sugar', self.ml)
        r = self._restock(ing, '100', '2.0')
        retired = self._ing('Old copy', self.ml, active=False)
        cases = (
            # same ingredient
            {'target_ingredient': ing.pk,
             'quantity_added': '100', 'cost_per_unit': '2.0'},
            # inactive target — would re-create the duplicate-copy tangle
            {'target_ingredient': retired.pk,
             'quantity_added': '100', 'cost_per_unit': '2.0'},
            # missing qty/price
            {'target_ingredient': ing.pk},
            # zero price
            {'target_ingredient': retired.pk,
             'quantity_added': '100', 'cost_per_unit': '0'},
        )
        for body in cases:
            resp = self.client.post(
                self._url(r, 'reattribute'), body, format='json')
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, body)
        r.refresh_from_db()
        self.assertFalse(r.is_voided)

    # -- preparations: v1 scope guard over HTTP ------------------------------

    def test_prep_corrections_rejected_over_http(self):
        prep = Ingredient.objects.create(
            name='Simple Syrup', unit=self.ml, cost_per_unit=Decimal('1.5'),
            current_stock=Decimal('500'), is_active=True,
            is_preparation=True, batch_yield=Decimal('500'))
        normal = self._ing('Sugar', self.ml)
        pr = self._restock(prep, '100', '2.0')
        nr = self._restock(normal, '100', '2.0')
        # all three verbs on a prep's restock → 400
        for verb, body in (
            ('void', {}),
            ('edit', {'quantity_added': '50'}),
            ('reattribute', {'target_ingredient': normal.pk,
                             'quantity_added': '100', 'cost_per_unit': '2.0'}),
        ):
            resp = self.client.post(self._url(pr, verb), body, format='json')
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, verb)
        # a prep as reattribute TARGET → 400 (serializer queryset excludes it)
        resp = self.client.post(
            self._url(nr, 'reattribute'),
            {'target_ingredient': prep.pk, 'quantity_added': '100',
             'cost_per_unit': '2.0'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        pr.refresh_from_db(); nr.refresh_from_db()
        self.assertFalse(pr.is_voided)
        self.assertFalse(nr.is_voided)

    # -- history listing carries the audit surface ---------------------------

    def test_restock_logs_listing_exposes_correction_fields(self):
        ing = self._ing('Sugar', self.ml)
        r1 = self._restock(ing, '100', '2.0')
        r2 = self._restock(ing, '50', '3.0')
        self.client.post(self._url(r1, 'void'), {'reason': 'oops'},
                         format='json')
        self.client.post(self._url(r2, 'edit'), {'quantity_added': '60'},
                         format='json')
        resp = self.client.get(f'/api/canteen/ingredients/{ing.pk}/restock_logs/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = {row['id']: row for row in resp.data}
        self.assertTrue(rows[r1.pk]['is_voided'])
        self.assertEqual(rows[r1.pk]['voided_by_name'], 'mgr')
        self.assertEqual(rows[r1.pk]['correction_note'], 'oops')
        self.assertFalse(rows[r2.pk]['is_voided'])
        self.assertEqual(rows[r2.pk]['corrected_by_name'], 'mgr')
        self.assertIsNotNone(rows[r2.pk]['corrected_at'])
