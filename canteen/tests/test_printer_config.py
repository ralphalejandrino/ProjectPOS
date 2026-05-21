"""ISSUE-099 — printer transport mode + paper/font calibration.

Covers the receipt_service refactor (mode gates printing; transport chosen
by mode; column count driven by paper_width x font) and the 0031 migration
data step that infers printer_mode from the legacy printer_ip.
"""

import importlib
from unittest import mock

from rest_framework.test import APITestCase

from canteen.models import BusinessProfile
from canteen import receipt_service


def _profile(**overrides):
    bp = BusinessProfile.get_instance()
    for k, v in overrides.items():
        setattr(bp, k, v)
    bp.save()
    return bp


class TransportModeTests(APITestCase):
    def test_disabled_mode_skips_print(self):
        """mode='disabled' returns failure without opening any transport."""
        _profile(printer_mode='disabled')
        with mock.patch.object(receipt_service, 'File') as f, \
             mock.patch.object(receipt_service, 'Network') as n:
            result = receipt_service.print_receipt(object())
        self.assertFalse(result['success'])
        f.assert_not_called()
        n.assert_not_called()

    def test_usb_mode_uses_file_transport(self):
        """mode='usb' builds a File transport on the local device, no Network."""
        bp = _profile(printer_mode='usb')
        with mock.patch.object(receipt_service, 'File') as f, \
             mock.patch.object(receipt_service, 'Network') as n, \
             mock.patch.object(receipt_service, '_get_usb_device_path',
                               return_value='/dev/usb/lp1'):
            transport = receipt_service._get_transport(bp)
        f.assert_called_once_with('/dev/usb/lp1')
        n.assert_not_called()
        self.assertIs(transport, f.return_value)

    def test_network_mode_uses_network_transport(self):
        """mode='network' builds a Network transport from ip:port, no File."""
        bp = _profile(printer_mode='network',
                      printer_ip='192.168.1.5', printer_port=9100)
        with mock.patch.object(receipt_service, 'File') as f, \
             mock.patch.object(receipt_service, 'Network') as n:
            transport = receipt_service._get_transport(bp)
        n.assert_called_once_with('192.168.1.5', port=9100)
        f.assert_not_called()
        self.assertIs(transport, n.return_value)


class ColumnCalibrationTests(APITestCase):
    def test_paper_width_font_drives_column_count(self):
        """_receipt_cols maps each (width, font) to its calibrated columns.

        FEATURE-040: corrected from the prior (wrong) values. Font A is 12 dots
        wide, Font B 9; 58mm = 384 dots, 80mm = 576.
        """
        cases = {
            ('58mm', 'A'): 32,   # 384 / 12
            ('58mm', 'B'): 42,   # 384 / 9
            ('80mm', 'A'): 48,   # 576 / 12
            ('80mm', 'B'): 64,   # 576 / 9
        }
        for (width, font), cols in cases.items():
            bp = _profile(paper_width=width, printer_font=font)
            self.assertEqual(receipt_service._receipt_cols(bp), cols,
                             f'{width}/{font} should be {cols} cols')

    def test_escpos_font_mapping(self):
        self.assertEqual(receipt_service._escpos_font(
            _profile(printer_font='A')), 'a')
        self.assertEqual(receipt_service._escpos_font(
            _profile(printer_font='B')), 'b')


class _StubApps:
    """Minimal apps registry for invoking a migration RunPython callable."""

    def get_model(self, app_label, model_name):
        return BusinessProfile


class MigrationDataStepTests(APITestCase):
    def test_migration_data_step_preserves_intent(self):
        """ip ''→disabled, 127.0.0.1→usb, real IP→network."""
        migration = importlib.import_module(
            'canteen.migrations.0031_printer_config_fields')

        # Start from a clean slate so get_instance()'s row doesn't interfere.
        BusinessProfile.objects.all().delete()
        empty = BusinessProfile.objects.create(printer_ip=None)
        usb = BusinessProfile.objects.create(printer_ip='127.0.0.1')
        net = BusinessProfile.objects.create(printer_ip='192.168.1.5')

        migration.set_printer_mode(_StubApps(), None)

        empty.refresh_from_db()
        usb.refresh_from_db()
        net.refresh_from_db()
        self.assertEqual(empty.printer_mode, 'disabled')
        self.assertEqual(usb.printer_mode, 'usb')
        self.assertEqual(net.printer_mode, 'network')
