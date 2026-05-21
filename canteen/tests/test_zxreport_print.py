"""ISSUE-115 — Z/X report print path shares the FEATURE-040 discipline.

Byte-verifies (via escpos Dummy) that Z and X reports, at every width/font:
  * select the chosen font and keep it (font A vs B byte-distinct on the body),
  * reset the body to normal size after the big header (no oversized body),
  * print currency with an ASCII token (ISSUE-114), never the ₱ glyph.
The legacy test_zreport_print uses a fake printer that ignores set(), so it
cannot see formatting — these tests use the real command stream.
"""
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from escpos.printer import Dummy

import canteen.receipt_service as rs
from canteen.models import BusinessProfile

ESC_M_A = b'\x1bM\x00'
ESC_M_B = b'\x1bM\x01'
TXT_NORMAL = b'\x1b!\x00'


def _profile(**over):
    bp = BusinessProfile.get_instance()
    bp.printer_mode = 'usb'
    bp.currency = 'PHP'
    bp.paper_width = '58mm'
    bp.printer_font = 'A'
    for k, v in over.items():
        setattr(bp, k, v)
    bp.save()
    return bp


def _zreport(**over):
    d = dict(
        currency='PHP', is_official=True,
        business_name='Test Cafe', business_address='Manila',
        business_tin='123-456-789', machine_identification_number='MIN-1',
        machine_serial_number='SN-1', pos_accreditation_number='ACC-1',
        pos_permit_number='PERM-1',
        z_counter=5, reset_counter=0, business_date=date(2026, 5, 22),
        started_at=datetime(2026, 5, 22, 8, 0),
        finalized_at=datetime(2026, 5, 22, 20, 0),
        cashier=SimpleNamespace(username='cash1'),
        first_or_number='OR-1', last_or_number='OR-9',
        voided_count=0, voided_or_numbers=[],
        gross_sales=Decimal('1000.00'), discount_total=Decimal('50.00'),
        sc_discount_total=Decimal('50.00'), pwd_discount_total=Decimal('0'),
        promo_discount_total=Decimal('0'), net_sales=Decimal('950.00'),
        vatable_sales=Decimal('848.21'), output_vat=Decimal('101.79'),
        vat_exempt_sales=Decimal('0'), zero_rated_sales=Decimal('0'),
        payment_breakdown={'cash': Decimal('950.00')},
        opening_cash=Decimal('1000.00'), cash_collected=Decimal('950.00'),
        cash_expected=Decimal('1950.00'), cash_counted=Decimal('1950.00'),
        over_short=Decimal('0.00'), grand_total_sales=Decimal('950.00'),
    )
    d.update(over)
    return SimpleNamespace(**d)


_XDATA = {
    'cashier': 'cash1', 'opened_at': datetime(2026, 5, 22, 8, 0),
    'gross_sales': Decimal('1000.00'), 'void_total': Decimal('0'),
    'net_sales': Decimal('1000.00'), 'transaction_count': 7,
    'by_payment_method': [{'payment_method': 'cash', 'count': 7,
                           'subtotal': Decimal('1000.00')}],
}


def _print_z(z):
    d = Dummy()
    with mock.patch.object(rs, 'File', return_value=d):
        rs.print_z_report(z)
    return d.output


def _print_x():
    d = Dummy()
    with mock.patch.object(rs, 'File', return_value=d):
        rs.print_xreport_summary(_XDATA)
    return d.output


class ZReportFormattingTests(TestCase):
    def test_font_a_b_distinct_all_widths(self):
        for width in ('58mm', '80mm'):
            _profile(paper_width=width, printer_font='A')
            out_a = _print_z(_zreport())
            _profile(paper_width=width, printer_font='B')
            out_b = _print_z(_zreport())
            self.assertIn(ESC_M_A, out_a, width)
            self.assertNotIn(ESC_M_B, out_a, width)
            self.assertIn(ESC_M_B, out_b, width)
            self.assertNotIn(ESC_M_A, out_b, width)
            self.assertGreater(out_b.count(ESC_M_B), 1, width)  # re-asserted

    def test_body_size_reset(self):
        _profile()
        self.assertIn(TXT_NORMAL, _print_z(_zreport()))

    def test_ascii_currency_not_glyph(self):
        _profile(currency='PHP')
        text = _print_z(_zreport()).decode('latin-1')
        self.assertIn('PHP ', text)
        self.assertNotIn('₱', text)

    def test_currency_code_from_snapshot(self):
        _profile()
        text = _print_z(_zreport(currency='USD')).decode('latin-1')
        self.assertIn('USD ', text)


class XReportFormattingTests(TestCase):
    def test_font_a_b_distinct(self):
        _profile(printer_font='A')
        out_a = _print_x()
        _profile(printer_font='B')
        out_b = _print_x()
        self.assertIn(ESC_M_A, out_a)
        self.assertNotIn(ESC_M_B, out_a)
        self.assertIn(ESC_M_B, out_b)
        self.assertNotIn(ESC_M_A, out_b)

    def test_body_size_reset(self):
        _profile()
        self.assertIn(TXT_NORMAL, _print_x())

    def test_ascii_currency_not_glyph(self):
        _profile(currency='PHP')
        text = _print_x().decode('latin-1')
        self.assertIn('PHP ', text)
        self.assertNotIn('₱', text)
