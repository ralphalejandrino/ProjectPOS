"""FEATURE-011-B — BusinessProfile BIR identity fields.

Covers the PATCH endpoint persistence, backward compatibility for
existing (all-blank) installs, ISO-date validation on
pos_accreditation_valid_until, and BusinessProfileSerializer round-trip.
"""

import datetime

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import BusinessProfile, User
from canteen.serializers import BusinessProfileSerializer

UPDATE_URL = '/api/canteen/business/update/'
GET_URL = '/api/canteen/business/'

BIR_PAYLOAD = {
    'machine_identification_number': 'MIN-0099887766',
    'machine_serial_number': 'SN-TARSIER-001',
    'pos_accreditation_number': 'ACCR-2026-12345',
    'pos_permit_number': 'PERMIT-AB-678',
    'pos_accreditation_valid_until': '2027-12-31',
}


class BirIdentityFieldTests(APITestCase):

    def setUp(self):
        self.manager = User.objects.create_user(
            username='mgr', password='x', role='manager'
        )
        self.profile = BusinessProfile.get_instance()
        self.client.force_authenticate(self.manager)

    def test_patch_persists_all_five_fields(self):
        """1. PATCH /api/business/update/ with the 5 new fields persists."""
        resp = self.client.patch(UPDATE_URL, BIR_PAYLOAD, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        p = BusinessProfile.get_instance()
        self.assertEqual(p.machine_identification_number, 'MIN-0099887766')
        self.assertEqual(p.machine_serial_number, 'SN-TARSIER-001')
        self.assertEqual(p.pos_accreditation_number, 'ACCR-2026-12345')
        self.assertEqual(p.pos_permit_number, 'PERMIT-AB-678')
        self.assertEqual(
            p.pos_accreditation_valid_until, datetime.date(2027, 12, 31)
        )

        # And the read endpoint surfaces them back.
        get_resp = self.client.get(GET_URL)
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        data = get_resp.json()
        self.assertEqual(data['machine_identification_number'], 'MIN-0099887766')
        self.assertEqual(data['pos_accreditation_valid_until'], '2027-12-31')

    def test_existing_blank_install_still_validates_and_saves(self):
        """2. Existing install with all 5 blank still validates and saves."""
        p = BusinessProfile.get_instance()
        self.assertEqual(p.machine_identification_number, '')
        self.assertEqual(p.machine_serial_number, '')
        self.assertEqual(p.pos_accreditation_number, '')
        self.assertEqual(p.pos_permit_number, '')
        self.assertIsNone(p.pos_accreditation_valid_until)

        # full_clean must not complain about the blank BIR fields.
        p.full_clean()

        # An unrelated PATCH still succeeds and leaves BIR fields blank.
        resp = self.client.patch(
            UPDATE_URL, {'business_name': 'Renamed Co'}, format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        p.refresh_from_db()
        self.assertEqual(p.business_name, 'Renamed Co')
        self.assertEqual(p.machine_identification_number, '')
        self.assertIsNone(p.pos_accreditation_valid_until)

    def test_valid_until_accepts_iso_rejects_garbage(self):
        """3. pos_accreditation_valid_until: ISO ok, garbage rejected."""
        ok = self.client.patch(
            UPDATE_URL,
            {'pos_accreditation_valid_until': '2030-01-15'},
            format='json',
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(
            BusinessProfile.get_instance().pos_accreditation_valid_until,
            datetime.date(2030, 1, 15),
        )

        bad = self.client.patch(
            UPDATE_URL,
            {'pos_accreditation_valid_until': 'not-a-date'},
            format='json',
        )
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        # Unchanged from the valid value above.
        self.assertEqual(
            BusinessProfile.get_instance().pos_accreditation_valid_until,
            datetime.date(2030, 1, 15),
        )

        # Empty string clears it back to NULL (not '').
        cleared = self.client.patch(
            UPDATE_URL,
            {'pos_accreditation_valid_until': ''},
            format='json',
        )
        self.assertEqual(cleared.status_code, status.HTTP_200_OK)
        self.assertIsNone(
            BusinessProfile.get_instance().pos_accreditation_valid_until
        )

    def test_serializer_round_trips_all_five_fields(self):
        """4. BusinessProfileSerializer round-trips all 5 fields."""
        profile = BusinessProfile.get_instance()
        ser = BusinessProfileSerializer(
            instance=profile, data=BIR_PAYLOAD, partial=True
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()

        out = BusinessProfileSerializer(BusinessProfile.get_instance()).data
        self.assertEqual(out['machine_identification_number'], 'MIN-0099887766')
        self.assertEqual(out['machine_serial_number'], 'SN-TARSIER-001')
        self.assertEqual(out['pos_accreditation_number'], 'ACCR-2026-12345')
        self.assertEqual(out['pos_permit_number'], 'PERMIT-AB-678')
        self.assertEqual(out['pos_accreditation_valid_until'], '2027-12-31')
