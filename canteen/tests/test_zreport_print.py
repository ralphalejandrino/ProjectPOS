"""FEATURE-011-D — thermal ZReport rendering.

print_z_report must build its ESC/POS stream exclusively from the
frozen ZReport instance (FLAG-058: printed Z and HTML Z parity), format
all money through canteen.utils.currency.format_currency, and never
raise on printer failure.
"""

from decimal import Decimal
from unittest import mock

from django.utils import timezone as dj_tz
from rest_framework.test import APITestCase

from canteen.models import BusinessProfile, Item, Shift, User
from canteen import receipt_service
from canteen.receipt_service import print_z_report
from canteen.services import close_shift_and_finalize_z, create_pos_transaction


class _FakePrinter:
    """Records every text() write; ignores formatting calls."""

    def __init__(self, *a, **k):
        self.lines = []

    def set(self, *a, **k):
        pass

    def text(self, s):
        self.lines.append(s)

    def cut(self, *a, **k):
        self.lines.append('<CUT>')

    def close(self):
        pass

    @property
    def output(self):
        return ''.join(self.lines)


def _bp(**overrides):
    bp = BusinessProfile.get_instance()
    bp.vat_enabled = True
    bp.vat_inclusive = True
    bp.vat_rate = Decimal('12.00')
    bp.sc_discount_rate = Decimal('20.00')
    bp.promo_discount_enabled = True
    bp.track_inventory = True
    bp.printer_enabled = True
    bp.machine_identification_number = 'MIN-001'
    bp.machine_serial_number = 'SN-001'
    bp.pos_accreditation_number = 'ACCR-9'
    bp.business_name = 'Test Canteen'
    bp.tin = '123-456-789'
    bp.address = 'Manila'
    bp.currency = 'PHP'
    for k, v in overrides.items():
        setattr(bp, k, v)
    bp.save()
    return bp


class ZPrintTestBase(APITestCase):
    def setUp(self):
        self.bp = _bp()
        self.user = User.objects.create_user(
            username='cash1', password='x', role='cashier'
        )
        self.item = Item.objects.create(
            name='Coffee', price=Decimal('100.00'), stock=10000
        )

    def _finalize(self, cash_counted=Decimal('200.00'), price=None, **sell):
        Shift.objects.create(
            cashier=self.user, opening_cash=Decimal('100.00'), is_open=True
        )
        item = self.item
        if price is not None:
            item = Item.objects.create(
                name=f'I{price}', price=Decimal(str(price)), stock=10000
            )
        create_pos_transaction(
            [{'item_id': item.id, 'quantity': 1}],
            sell.pop('payment_method', 'cash'), cashier=self.user, **sell
        )
        shift = Shift.objects.get(cashier=self.user, is_open=True)
        return close_shift_and_finalize_z(shift.id, cash_counted, self.user)

    def _print(self, z):
        fake = _FakePrinter()
        with mock.patch.object(receipt_service, 'File', return_value=fake):
            ok = print_z_report(z)
        return ok, fake.output


class StreamLayoutTests(ZPrintTestBase):
    def test_sections_appear_in_order(self):
        """1. Header / Z block / sales / payments / reconciliation / footer."""
        z = self._finalize()
        ok, out = self._print(z)
        self.assertTrue(ok)

        markers = [
            'Test Canteen',                       # header business name
            'TIN: 123-456-789',
            'MIN: MIN-001',
            'Serial: SN-001',
            'Accreditation: ACCR-9',
            'Z REPORT',
            f'Z #: {z.z_counter}',
            f'Business Date: {z.business_date}',
            'Cashier: cash1',
            'Gross Sales:',
            'Net Sales:',
            'Vatable Sales:',
            'Output VAT:',
            'VAT-Exempt Sales:',
            'PAYMENTS:',
            'Total Payments:',
            'Opening Cash:',
            'Cash Expected:',
            'Cash Counted:',
            'Over/Short:',
            'Grand Total Sales:',
            'Generated ',
            '<CUT>',
        ]
        last = -1
        for m in markers:
            idx = out.find(m, last + 1)
            self.assertNotEqual(idx, -1, f'missing/out-of-order: {m!r}')
            last = idx

    def test_currency_via_format_currency(self):
        """2. Money uses format_currency (no hardcoded prefixes)."""
        z = self._finalize()
        ok, out = self._print(z)
        self.assertTrue(ok)
        # PHP renders the ₱ symbol; no bare 'PHP ' / raw float lines.
        self.assertIn('₱', out)
        self.assertNotIn('PHP ', out)
        with mock.patch('canteen.receipt_service.format_currency',
                        wraps=receipt_service.format_currency) as fc:
            self._print(z)
        self.assertTrue(fc.called)
        # Currency code passed through is the frozen ZReport.currency.
        for call in fc.call_args_list:
            self.assertEqual(call.args[1], z.currency)

    def test_reads_from_zreport_not_live_bp(self):
        """3. FLAG-058 regression: live BP mutation must not leak in."""
        z = self._finalize()
        self.bp.business_name = 'MUTATED LIVE NAME'
        self.bp.currency = 'USD'
        self.bp.save()
        ok, out = self._print(z)
        self.assertTrue(ok)
        self.assertIn('Test Canteen', out)
        self.assertNotIn('MUTATED LIVE NAME', out)
        # currency frozen as PHP -> ₱, never the live USD '$'
        self.assertIn('₱', out)
        self.assertNotIn('$', out)

    def test_vat_exempt_line(self):
        """4. VAT-exempt rows surface a correct vat_exempt_sales line."""
        z = self._finalize(
            price='112.00',
            payment_method='cash',
            discount_amount=Decimal('20.00'),
            discount_type='sc',
            discount_id_number='SC-1',
        )
        self.assertGreater(z.vat_exempt_sales, Decimal('0'))
        ok, out = self._print(z)
        self.assertTrue(ok)
        from canteen.utils.currency import format_currency
        self.assertIn(
            format_currency(z.vat_exempt_sales, z.currency), out
        )

    def test_official_z_includes_identity_lines(self):
        """6. ISSUE-105: official Z prints MIN/Serial/Accreditation."""
        z = self._finalize()
        self.assertTrue(z.is_official)
        ok, out = self._print(z)
        self.assertTrue(ok)
        self.assertIn('MIN: MIN-001', out)
        self.assertIn('Serial: SN-001', out)
        self.assertIn('Accreditation: ACCR-9', out)
        self.assertNotIn('UNOFFICIAL', out)


class UnofficialZPrintTests(ZPrintTestBase):
    def setUp(self):
        super().setUp()
        self.bp = _bp(machine_identification_number='')

    def test_unofficial_banners_top_and_bottom(self):
        """7. ISSUE-105: UNOFFICIAL banner at top and bottom."""
        z = self._finalize()
        self.assertFalse(z.is_official)
        ok, out = self._print(z)
        self.assertTrue(ok)
        self.assertIn('NOT FOR BIR SUBMISSION', out)
        self.assertIn('*** UNOFFICIAL Z REPORT ***', out)
        # top banner precedes the business name; bottom follows the footer
        self.assertLess(out.find('UNOFFICIAL'), out.find('Test Canteen'))
        self.assertLess(out.find('Generated '),
                        out.find('*** UNOFFICIAL Z REPORT ***'))

    def test_unofficial_omits_identity_lines(self):
        """8. ISSUE-105: no MIN/Serial/Accreditation/Permit rows."""
        z = self._finalize()
        ok, out = self._print(z)
        self.assertTrue(ok)
        self.assertNotIn('MIN:', out)
        self.assertNotIn('Serial:', out)
        self.assertNotIn('Accreditation:', out)
        self.assertNotIn('Permit:', out)
        # rest still renders
        self.assertIn('Test Canteen', out)
        self.assertIn('Gross Sales:', out)


class PrinterErrorTests(ZPrintTestBase):
    def test_printer_offline_returns_false(self):
        """5. Printer error -> False, logged, never raised."""
        z = self._finalize()
        with mock.patch.object(
            receipt_service, 'File', side_effect=OSError('printer offline')
        ):
            with self.assertLogs('canteen.receipt_service', level='WARNING'):
                result = print_z_report(z)
        self.assertFalse(result)
