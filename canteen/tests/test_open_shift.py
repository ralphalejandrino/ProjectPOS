"""ISSUE-107 / ISSUE-104 — open-shift endpoint + per-cashier constraint.

Covers POST /api/canteen/shifts/open/ and GET /api/canteen/shifts/current/,
plus the one_open_shift_per_cashier partial unique constraint behaviour.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import Shift, User

OPEN_URL = '/api/canteen/shifts/open/'
CURRENT_URL = '/api/canteen/shifts/current/'


class OpenShiftTests(APITestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(
            username='ana', password='x', role='cashier'
        )
        self.other = User.objects.create_user(
            username='ben', password='x', role='cashier'
        )

    # 1. valid opening_cash -> 201 + Shift JSON
    def test_open_valid(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.post(OPEN_URL, {'opening_cash': '500.00'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['is_open'])
        self.assertEqual(Decimal(str(resp.data['opening_cash'])), Decimal('500.00'))
        self.assertEqual(resp.data['cashier_name'], 'ana')

    # 2. opening_cash < 0 -> 400
    def test_open_negative(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.post(OPEN_URL, {'opening_cash': '-1'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Shift.objects.filter(cashier=self.cashier).exists())

    # 3. already has an open shift -> 400 with the specific message
    def test_open_when_already_open(self):
        Shift.objects.create(cashier=self.cashier, opening_cash=Decimal('0'),
                             is_open=True)
        self.client.force_authenticate(self.cashier)
        resp = self.client.post(OPEN_URL, {'opening_cash': '100'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already have an open shift', resp.data['error'])
        self.assertEqual(Shift.objects.filter(cashier=self.cashier).count(), 1)

    # 4. GET current when shift open -> 200 + that shift
    def test_current_when_open(self):
        shift = Shift.objects.create(cashier=self.cashier,
                                     opening_cash=Decimal('250'), is_open=True)
        self.client.force_authenticate(self.cashier)
        resp = self.client.get(CURRENT_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['id'], shift.id)

    # 5. GET current when none -> 204 No Content
    def test_current_when_none(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.get(CURRENT_URL)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    # 6. Two cashiers each have one open shift simultaneously
    def test_per_cashier_independent(self):
        self.client.force_authenticate(self.cashier)
        r1 = self.client.post(OPEN_URL, {'opening_cash': '100'}, format='json')
        self.client.force_authenticate(self.other)
        r2 = self.client.post(OPEN_URL, {'opening_cash': '200'}, format='json')
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Shift.objects.filter(is_open=True).count(), 2)

    # 7. close then open again works (no constraint violation post-close)
    def test_close_then_reopen(self):
        old = Shift.objects.create(cashier=self.cashier,
                                   opening_cash=Decimal('0'), is_open=True)
        old.is_open = False
        old.save(update_fields=['is_open'])
        self.client.force_authenticate(self.cashier)
        resp = self.client.post(OPEN_URL, {'opening_cash': '300'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Shift.objects.filter(cashier=self.cashier).count(), 2)
        self.assertEqual(
            Shift.objects.filter(cashier=self.cashier, is_open=True).count(), 1
        )
