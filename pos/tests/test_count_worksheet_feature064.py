"""FEATURE-064 — printed worksheet for the weekly physical count.

Ralph's idea (2026-07-21). The stock is not next to the screen, so the manager
walks back and forth to read each system figure — which is how a count gets
rushed or transcribed wrong. A printed strip with a write-in line lets the whole
count happen at the shelf and be keyed in once.

The flagged set is computed SERVER-side and must match the Weekly Count tab's
predicate exactly (FEATURE-055): counted-type items, plus anything at or below
par. Two implementations of "what needs counting" would drift and the manager
would be counting a different set than the app expects back.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from pos.models import (
    BusinessProfile, Ingredient, IngredientUnit, User,
)
from pos.receipt_service import build_count_worksheet_lines
from pos.services import ingredients_flagged_for_count


class FlaggedSetTests(APITestCase):
    def setUp(self):
        bp = BusinessProfile.get_instance()
        bp.printer_mode = 'usb'
        bp.business_name = 'PROD'
        bp.save()
        self.g = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})[0]
        self.sack = IngredientUnit.objects.get_or_create(
            abbreviation='sack', defaults={'name': 'Sack'})[0]

    def _ing(self, name, stock, par, track=True, purchase_unit=None):
        return Ingredient.objects.create(
            name=name, unit=self.g, cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal(str(stock)), par_level=Decimal(str(par)),
            track_depletion=track, purchase_unit=purchase_unit,
        )

    def test_counted_type_is_always_flagged(self):
        self._ing('Napkins', stock=999, par=0, track=False)
        names = [r['name'] for r in ingredients_flagged_for_count()]
        self.assertEqual(names, ['Napkins'])

    def test_at_or_below_par_is_flagged(self):
        self._ing('Blacktea', stock=5, par=10)
        self._ing('Exactly at par', stock=10, par=10)
        names = sorted(r['name'] for r in ingredients_flagged_for_count())
        self.assertEqual(names, ['Blacktea', 'Exactly at par'])

    def test_NEGATIVE_CONTROL_healthy_tracked_item_is_NOT_flagged(self):
        """Without this, a predicate that flagged everything would pass the
        two tests above."""
        self._ing('Sugar', stock=500, par=10)
        self.assertEqual(ingredients_flagged_for_count(), [])

    def test_par_level_zero_does_not_flag_a_tracked_item(self):
        """par 0 means 'no par set', not 'always flag'. current_stock <= 0
        would otherwise drag every zero-stock item in forever."""
        self._ing('Unset par', stock=0, par=0)
        self.assertEqual(ingredients_flagged_for_count(), [])

    def test_inactive_ingredients_are_excluded(self):
        i = self._ing('Retired syrup', stock=0, par=5)
        i.is_active = False
        i.save()
        self.assertEqual(ingredients_flagged_for_count(), [])

    def test_row_carries_unit_and_package(self):
        self._ing('Flour', stock=2, par=10, purchase_unit=self.sack)
        row = ingredients_flagged_for_count()[0]
        self.assertEqual(row['unit'], 'g')
        self.assertEqual(row['package'], 'sack')
        self.assertEqual(row['system_qty'], Decimal('2.0000'))


class WorksheetLayoutTests(APITestCase):
    def setUp(self):
        bp = BusinessProfile.get_instance()
        bp.printer_mode = 'usb'
        bp.business_name = 'PROD'
        bp.save()
        self.g = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})[0]

    def test_worksheet_has_a_write_in_line_per_item(self):
        """The write-in line IS the feature — without it this is just a list."""
        rows = [{'name': 'Blacktea', 'unit': 'g', 'package': '',
                 'system_qty': Decimal('5'), 'counted_type': False}]
        out = '\n'.join(build_count_worksheet_lines(rows))

        self.assertIn('Blacktea', out)
        self.assertIn('system: 5 g', out)
        self.assertIn('counted:', out)
        self.assertIn('___', out)

    def test_counted_type_is_marked(self):
        rows = [{'name': 'Napkins', 'unit': 'pcs', 'package': '',
                 'system_qty': Decimal('12'), 'counted_type': True}]
        out = '\n'.join(build_count_worksheet_lines(rows))
        self.assertIn('Napkins *', out)

    def test_empty_list_still_prints_something_useful(self):
        """A blank strip would look like a printer fault."""
        out = '\n'.join(build_count_worksheet_lines([]))
        self.assertIn('Nothing flagged', out)

    def test_long_names_do_not_overrun_the_paper(self):
        from pos.receipt_service import _receipt_cols
        bp = BusinessProfile.get_instance()
        width = _receipt_cols(bp)
        rows = [{'name': 'X' * 200, 'unit': 'g', 'package': '',
                 'system_qty': Decimal('1'), 'counted_type': False}]
        for ln in build_count_worksheet_lines(rows, bp):
            self.assertLessEqual(len(ln), width, ln)


class WorksheetEndpointTests(APITestCase):
    def setUp(self):
        bp = BusinessProfile.get_instance()
        bp.printer_mode = 'disabled'   # print is fire-and-forget + non-fatal
        bp.save()
        self.manager = User.objects.create_user(
            username='m1', password='x', role='manager')
        self.cashier = User.objects.create_user(
            username='c1', password='x', role='cashier')
        g = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})[0]
        Ingredient.objects.create(
            name='Blacktea', unit=g, cost_per_unit=Decimal('1.0000'),
            current_stock=Decimal('5'), par_level=Decimal('10'),
        )

    URL = '/api/pos/ingredients/count-worksheet/print/'

    def test_manager_can_queue_the_worksheet(self):
        self.client.force_authenticate(user=self.manager)
        res = self.client.post(self.URL, {}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 1)

    def test_cashier_cannot(self):
        self.client.force_authenticate(user=self.cashier)
        res = self.client.post(self.URL, {}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_a_disabled_printer_does_not_error_the_request(self):
        """Non-fatal contract: the ingredients page must not break because the
        printer is off."""
        self.client.force_authenticate(user=self.manager)
        res = self.client.post(self.URL, {}, format='json')
        self.assertEqual(res.status_code, 200)
