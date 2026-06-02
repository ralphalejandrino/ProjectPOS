"""FEATURE-035 — post-accreditation Z-counter reset.

When the café receives BIR accreditation the official Z-series restarts at
#1: z_counter -> 0 and grand_total -> 0 on the singleton ZCounter, the
accreditation event is stamped on it, and every existing (pre-accreditation)
ZReport is flagged is_official=False. The action is one-time (a second
attempt is rejected) and admin/staff only.
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, Item, Shift, User, ZCounter, ZReport,
)
from canteen.services import (
    AccreditationAlreadyApplied, apply_accreditation_reset,
    close_shift_and_finalize_z, create_pos_transaction,
)


def _bp():
    bp = BusinessProfile.get_instance()
    bp.vat_enabled = True
    bp.vat_inclusive = True
    bp.vat_rate = Decimal('12.00')
    bp.track_inventory = True
    bp.printer_mode = 'disabled'
    bp.machine_identification_number = 'MIN-001'
    bp.business_name = 'Test Canteen'
    bp.tin = '123-456-789'
    bp.address = 'Manila'
    bp.save()
    return bp


class AccreditationResetTests(APITestCase):
    def setUp(self):
        self.bp = _bp()
        self.admin = User.objects.create_user(
            username='admin1', password='x', role='admin'
        )
        self.cashier = User.objects.create_user(
            username='cash1', password='x', role='cashier'
        )
        self.item = Item.objects.create(
            name='Coffee', price=Decimal('112.00'), stock=10000
        )

    def _close_a_shift(self, cashier=None):
        cashier = cashier or self.cashier
        shift = Shift.objects.create(
            cashier=cashier, opening_cash=Decimal('100.00'), is_open=True
        )
        create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}], 'cash', cashier=cashier
        )
        return close_shift_and_finalize_z(shift.id, Decimal('212.00'), cashier)

    def test_reset_zeroes_counter_and_flags_existing_reports(self):
        """Reset → z_counter=0, grand_total=0, accredited_at set, Z reports pre-acc."""
        z1 = self._close_a_shift()
        z2 = self._close_a_shift()
        self.assertEqual([z1.z_counter, z2.z_counter], [1, 2])

        counter = apply_accreditation_reset(self.admin)

        self.assertEqual(counter.z_counter, 0)
        self.assertEqual(counter.grand_total, Decimal('0'))
        self.assertIsNotNone(counter.accredited_at)
        self.assertEqual(counter.reset_by, self.admin)

        # Pre-accreditation ZReports keep their z_counter but are flagged.
        self.assertFalse(ZReport.objects.get(pk=z1.pk).is_official)
        self.assertFalse(ZReport.objects.get(pk=z2.pk).is_official)

    def test_second_reset_raises(self):
        """A second reset attempt is rejected at the service level."""
        apply_accreditation_reset(self.admin)
        with self.assertRaises(AccreditationAlreadyApplied):
            apply_accreditation_reset(self.admin)

    def test_post_reset_finalize_is_official_z1(self):
        """First Z after reset → z_counter=1, is_official=True, grand_total reset."""
        self._close_a_shift()             # pre-accreditation Z-1
        apply_accreditation_reset(self.admin)

        z = self._close_a_shift()         # first official Z
        self.assertEqual(z.z_counter, 1)
        self.assertTrue(z.is_official)
        # grand_total restarted from 0, accumulating only this official Z.
        self.assertEqual(z.grand_total_sales, Decimal('112.00'))


class AccreditationEndpointTests(APITestCase):
    def setUp(self):
        self.bp = _bp()
        self.admin = User.objects.create_user(
            username='admin1', password='x', role='admin'
        )
        self.cashier = User.objects.create_user(
            username='cash1', password='x', role='cashier'
        )

    def test_status_then_reset_then_second_reset_400(self):
        self.client.force_authenticate(self.admin)

        # Initial status: not accredited.
        res = self.client.get(reverse('accreditation-status'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['accredited'])

        # Apply reset.
        res = self.client.post(reverse('accreditation-reset'), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['accredited'])
        self.assertEqual(res.data['reset_by'], 'admin1')
        self.assertIn('message', res.data)

        # Second reset → 400.
        res = self.client.post(reverse('accreditation-reset'), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already applied', res.data['error'])

        # Status now reflects accreditation.
        res = self.client.get(reverse('accreditation-status'))
        self.assertTrue(res.data['accredited'])
        self.assertEqual(res.data['reset_by'], 'admin1')

    def test_cashier_forbidden(self):
        self.client.force_authenticate(self.cashier)
        res = self.client.post(reverse('accreditation-reset'), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        # No accreditation applied.
        self.assertIsNone(ZCounter.objects.filter(pk=1).first() and
                          ZCounter.objects.get(pk=1).accredited_at)
