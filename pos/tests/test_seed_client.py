"""DEPLOY-003 — seed_client fresh-install seeder.

Covers the BusinessProfile shape (unofficial mode), the OR-series reset,
account creation with correct roles, the live-box guard (refuse without
--force / proceed with --force), and the no-demo-data guarantee.
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from pos.models import (
    BusinessProfile, Item, ItemCategory, OfficialReceiptCounter,
    PosTransaction, User,
)


def _run(**kwargs):
    out = StringIO()
    opts = {'business_name': 'PROD Cafe', 'or_prefix': 'OR-PROD', 'stdout': out}
    opts.update(kwargs)
    call_command('seed_client', **opts)
    return out.getvalue()


class SeedClientTests(TestCase):

    def test_creates_single_business_profile_with_blank_bir_fields(self):
        _run()
        self.assertEqual(BusinessProfile.objects.count(), 1)
        bp = BusinessProfile.objects.get()
        self.assertEqual(bp.business_name, 'PROD Cafe')
        self.assertEqual(bp.address, '')
        self.assertEqual(bp.currency, 'PHP')
        self.assertEqual(bp.tin, '')
        self.assertEqual(bp.machine_identification_number, '')
        self.assertEqual(bp.machine_serial_number, '')
        self.assertEqual(bp.pos_accreditation_number, '')
        self.assertEqual(bp.pos_permit_number, '')
        self.assertIsNone(bp.pos_accreditation_valid_until)

    def test_is_official_resolves_false_after_seed(self):
        _run()
        bp = BusinessProfile.objects.get()
        # Mirrors services.close_shift_and_finalize_z: blank MIN => UNOFFICIAL Z.
        is_official = bool(bp and (bp.machine_identification_number or '').strip())
        self.assertFalse(is_official)

    def test_or_series_reset_so_counter_starts_at_0001(self):
        # Pre-existing counter rows should be wiped by the seed.
        OfficialReceiptCounter.objects.create(date='2020-01-01', counter=42)
        _run()
        self.assertEqual(OfficialReceiptCounter.objects.count(), 0)
        # First receipt generated afterwards is sequence 0001.
        or_number = PosTransaction._generate_or_number()
        self.assertTrue(or_number.endswith('-0001'))

    def test_or_prefix_recorded_in_output(self):
        out = _run(or_prefix='OR-XYZ')
        self.assertIn('OR-XYZ', out)

    def test_creates_one_admin_and_n_cashiers_with_roles(self):
        _run(cashiers=3, admin_user='boss')
        self.assertEqual(User.objects.filter(role='admin').count(), 1)
        self.assertEqual(User.objects.filter(role='cashier').count(), 3)
        self.assertEqual(User.objects.get(username='boss').role, 'admin')
        for i in range(1, 4):
            self.assertEqual(User.objects.get(username=f'cashier{i}').role, 'cashier')

    def test_default_cashier_count_is_two(self):
        _run()
        self.assertEqual(User.objects.filter(role='cashier').count(), 2)

    def test_refuses_when_business_profile_exists_without_force(self):
        BusinessProfile.objects.create(business_name='Live Box')
        with self.assertRaises(CommandError):
            _run()
        # Untouched: still the original live profile, no accounts seeded.
        self.assertEqual(BusinessProfile.objects.count(), 1)
        self.assertEqual(BusinessProfile.objects.get().business_name, 'Live Box')
        self.assertFalse(User.objects.filter(username='cashier1').exists())

    def test_proceeds_with_force_when_profile_exists(self):
        BusinessProfile.objects.create(business_name='Live Box')
        _run(force=True)
        self.assertEqual(BusinessProfile.objects.count(), 1)
        self.assertEqual(BusinessProfile.objects.get().business_name, 'PROD Cafe')
        self.assertTrue(User.objects.filter(username='cashier1', role='cashier').exists())

    def test_no_demo_items_or_categories_created(self):
        _run()
        self.assertEqual(ItemCategory.objects.count(), 0)
        self.assertEqual(Item.objects.count(), 0)
        self.assertEqual(PosTransaction.objects.count(), 0)

    def test_pin_printed_in_handoff_block(self):
        out = _run()
        self.assertIn('HAND OFF THESE CREDENTIALS', out)
        self.assertIn('not stored in git', out)
