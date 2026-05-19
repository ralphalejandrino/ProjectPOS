"""FEATURE-011-D — POST /api/canteen/shifts/<id>/close/.

Exercises the atomic close-shift -> immutable ZReport endpoint that
replaces the legacy detail=False /shifts/close/ action.
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import BusinessProfile, Item, Shift, User
from canteen.services import create_pos_transaction


def _bp(**overrides):
    bp = BusinessProfile.get_instance()
    bp.vat_enabled = True
    bp.vat_inclusive = True
    bp.vat_rate = Decimal('12.00')
    bp.printer_enabled = False
    bp.track_inventory = True
    bp.machine_identification_number = 'MIN-001'
    bp.business_name = 'Test Canteen'
    bp.currency = 'PHP'
    for k, v in overrides.items():
        setattr(bp, k, v)
    bp.save()
    return bp


class CloseShiftEndpointTests(APITestCase):
    def setUp(self):
        self.bp = _bp()
        self.cashier = User.objects.create_user(
            username='cash1', password='x', role='cashier'
        )
        self.other = User.objects.create_user(
            username='cash2', password='x', role='cashier'
        )
        self.item = Item.objects.create(
            name='Coffee', price=Decimal('100.00'), stock=10000
        )

    def _open_and_sell(self, user=None):
        user = user or self.cashier
        shift = Shift.objects.create(
            cashier=user, opening_cash=Decimal('100.00'), is_open=True
        )
        create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}],
            'cash', cashier=user
        )
        return shift

    def _url(self, shift):
        return reverse('shift-close', args=[shift.pk])

    def test_valid_cash_counted_returns_201_zreport(self):
        """6. valid cash_counted -> 201 + ZReport JSON."""
        shift = self._open_and_sell()
        self.client.force_authenticate(self.cashier)
        res = self.client.post(self._url(shift),
                                {'cash_counted': '200.00'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('z_counter', res.data)
        self.assertEqual(res.data['cash_counted'], '200.00')
        shift.refresh_from_db()
        self.assertFalse(shift.is_open)

    def test_missing_cash_counted_returns_400(self):
        """7. no cash_counted -> 400 validation."""
        shift = self._open_and_sell()
        self.client.force_authenticate(self.cashier)
        res = self.client.post(self._url(shift), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cash_counted', res.data['error'])
        shift.refresh_from_db()
        self.assertTrue(shift.is_open)

    def test_min_blank_returns_201_unofficial(self):
        """8. ISSUE-105: MIN blank -> 201 with is_official=False."""
        self.bp.machine_identification_number = ''
        self.bp.save()
        shift = self._open_and_sell()
        self.client.force_authenticate(self.cashier)
        res = self.client.post(self._url(shift),
                                {'cash_counted': '200.00'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertFalse(res.data['is_official'])

    def test_wrong_cashier_returns_403(self):
        """9. not own shift, not manager/admin -> 403."""
        shift = self._open_and_sell(user=self.cashier)
        self.client.force_authenticate(self.other)
        res = self.client.post(self._url(shift),
                                {'cash_counted': '200.00'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        shift.refresh_from_db()
        self.assertTrue(shift.is_open)

    def test_already_closed_shift_returns_400(self):
        """10. already-closed shift -> 400."""
        shift = self._open_and_sell()
        self.client.force_authenticate(self.cashier)
        first = self.client.post(self._url(shift),
                                 {'cash_counted': '200.00'}, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post(self._url(shift),
                                  {'cash_counted': '200.00'}, format='json')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already closed', second.data['error'].lower())
