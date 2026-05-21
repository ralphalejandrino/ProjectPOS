"""FEATURE-040 — receipt layout, ESC/POS font/size/logo, and screen↔paper parity.

Uses escpos's Dummy printer to byte-verify the command stream (no hardware).
The on-screen preview and the printer both render from canteen.receipt_layout,
so the parity test asserts they emit the same rows.
"""
import io
import tempfile
from datetime import datetime
from decimal import Decimal
from unittest import mock

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from escpos.printer import Dummy

import canteen.receipt_service as rs
from canteen import receipt_layout
from canteen.models import BusinessProfile


# --- lightweight transaction stand-in (no DB rows needed for layout) ---------
class _Money:
    def __init__(self, a):
        self.amount = a


class _Variants:
    def __init__(self, vs):
        self._vs = vs

    def all(self):
        return self._vs


class _Item:
    def __init__(self, name, qty, price, sub, vs=()):
        self.quantity, self.unit_price = qty, price
        self.subtotal = _Money(Decimal(str(sub)))
        self.item = type('I', (), {'name': name})()
        self.variant_selections = _Variants(list(vs))


class _Items:
    def __init__(self, items):
        self._items = items

    def select_related(self, *a):
        return self

    def all(self):
        return self._items


def _txn(items=None, **over):
    class T:
        transaction_no = 'OR-000123'
        created_at = datetime(2026, 5, 22, 14, 30)
        cashier = type('C', (), {'username': 'cashier1'})()
        discount_amount = Decimal('0')
        discount_type = 'none'
        vat_exempt = False
        vat_amount = 0
        payment_method = 'cash'
        gcash_reference = None
        maya_reference = None
        customer_phone = None
        total_amount = _Money(Decimal('200.00'))
        cash_received = _Money(Decimal('500.00'))

        def get_payment_method_display(self):
            return 'Cash'
    t = T()
    t.items = _Items(items if items is not None
                     else [_Item('Brewed Coffee', 2, Decimal('100.00'), '200.00')])
    for k, v in over.items():
        setattr(t, k, v)
    return t


def _profile(**over):
    bp = BusinessProfile.get_instance()
    bp.printer_mode = 'usb'
    bp.business_name = 'Test Cafe'
    bp.currency = 'PHP'
    bp.paper_width = '58mm'
    bp.printer_font = 'A'
    for k, v in over.items():
        setattr(bp, k, v)
    bp.save()
    return bp


def _print(txn):
    d = Dummy()
    with mock.patch.object(rs, 'File', return_value=d):
        result = rs.print_receipt(txn)
    return d.output, result


ESC_M_A = b'\x1bM\x00'   # SET_FONT a (Font A)
ESC_M_B = b'\x1bM\x01'   # SET_FONT b (Font B)
TXT_NORMAL = b'\x1b!\x00'
GS_V0 = b'\x1dv0'        # raster bit image (logo)


class ReceiptColsTests(TestCase):
    def test_corrected_columns_per_width_font(self):
        cases = {('58mm', 'A'): 32, ('58mm', 'B'): 42,
                 ('80mm', 'A'): 48, ('80mm', 'B'): 64}
        for (w, f), expected in cases.items():
            bp = _profile(paper_width=w, printer_font=f)
            self.assertEqual(receipt_layout.receipt_cols(bp), expected, f'{w}/{f}')

    def test_none_profile_falls_back(self):
        self.assertEqual(receipt_layout.receipt_cols(None), 32)


class ReceiptFontTests(TestCase):
    def test_font_a_and_b_are_genuinely_distinct(self):
        _profile(printer_font='A')
        out_a, _ = _print(_txn())
        _profile(printer_font='B')
        out_b, _ = _print(_txn())
        self.assertNotEqual(out_a, out_b)
        # Font A stream selects font 0 and never font 1, and vice versa.
        self.assertIn(ESC_M_A, out_a)
        self.assertNotIn(ESC_M_B, out_a)
        self.assertIn(ESC_M_B, out_b)
        self.assertNotIn(ESC_M_A, out_b)

    def test_font_is_reasserted_after_header_reset(self):
        # The header's double-size set() emits ESC ! which resets to Font A;
        # the fix re-asserts font on every set(), so it appears many times.
        _profile(printer_font='B')
        out, _ = _print(_txn())
        self.assertGreater(out.count(ESC_M_B), 1)


class ReceiptSizeTests(TestCase):
    def test_body_size_is_reset_to_normal(self):
        # Regression: old code left the body double-sized (never reset). The
        # title block must be followed by a normal-size reset (TXT_NORMAL).
        _profile()
        out, _ = _print(_txn())
        self.assertIn(TXT_NORMAL, out)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ReceiptLogoTests(TestCase):
    def _attach_logo(self, bp):
        buf = io.BytesIO()
        from PIL import Image
        Image.new('L', (300, 100), 255).save(buf, 'PNG')
        bp.logo.save('logo.png', ContentFile(buf.getvalue()), save=True)

    def test_logo_rasterized_when_present(self):
        bp = _profile()
        self._attach_logo(bp)
        out, _ = _print(_txn())
        self.assertIn(GS_V0, out)

    def test_no_logo_no_raster(self):
        bp = _profile()
        if bp.logo:
            bp.logo.delete(save=True)
        out, _ = _print(_txn())
        self.assertNotIn(GS_V0, out)


class ReceiptCurrencyTests(TestCase):
    def test_currency_and_rates_come_from_profile(self):
        _profile(currency='PHP', vat_enabled=False,
                 sc_discount_rate=Decimal('20.00'))
        txn = _txn(discount_amount=Decimal('40.00'), discount_type='sc',
                   discount_id_number='SC-1')
        out, _ = _print(txn)
        text = out.decode('latin-1')
        self.assertNotIn('₱', text)        # never the hardcoded ₱ glyph
        self.assertIn('SC Discount (20%):', text)  # rate pulled from profile

    def test_changing_sc_rate_changes_label(self):
        _profile(sc_discount_rate=Decimal('25.00'))
        txn = _txn(discount_amount=Decimal('50.00'), discount_type='sc')
        out, _ = _print(txn)
        self.assertIn('SC Discount (25%):', out.decode('latin-1'))


class ReceiptAsciiCurrencyTests(TestCase):
    """ISSUE-114: print uses an ASCII currency token; screen keeps ₱."""

    def test_print_uses_iso_prefix_not_glyph(self):
        _profile(currency='PHP', vat_enabled=False)
        out, _ = _print(_txn())
        text = out.decode('latin-1')
        self.assertIn('PHP ', text)     # ASCII token "PHP 200.00"
        self.assertNotIn('₱', text)

    def test_print_uses_profile_currency_code(self):
        # Zero-config store with a different currency must still work.
        _profile(currency='USD', vat_enabled=False)
        out, _ = _print(_txn())
        self.assertIn('USD ', out.decode('latin-1'))

    def test_screen_preview_keeps_unicode_symbol(self):
        bp = _profile(currency='PHP')
        text = receipt_layout.render_text(_txn(), bp)
        self.assertIn('₱', text)        # browser-safe glyph stays on screen
        self.assertNotIn('PHP ', text)


class ReceiptParityTests(TestCase):
    """Screen and paper render from the SAME builder (FLAG-064)."""

    def test_printer_renders_from_shared_builder(self):
        # Parity by construction: the printer must render from the same
        # build_receipt_rows the preview uses. (Currency glyphs are not byte-
        # compared because the PC437 printer transliterates ₱ — see ticket note.)
        bp = _profile(printer_font='A')
        txn = _txn()
        with mock.patch.object(rs, 'build_receipt_rows',
                               wraps=receipt_layout.build_receipt_rows) as spy:
            _print(txn)
        spy.assert_called_once()

    def test_printed_stream_contains_ascii_builder_rows(self):
        bp = _profile(printer_font='A')
        txn = _txn(items=[
            _Item('Brewed Coffee', 2, Decimal('100.00'), '200.00'),
            _Item('A Very Long Item Name That Will Wrap Around', 1,
                  Decimal('55.00'), '55.00'),
        ])
        rows, cols, _ = receipt_layout.build_receipt_rows(txn, bp)
        printed = _print(txn)[0].decode('latin-1')
        # Non-currency rows must appear verbatim (dividers, item names, labels).
        for r in rows:
            t = r['text']
            if t.strip() and '₱' not in t and 'PHP' not in t and 'Cash:' not in t:
                self.assertIn(t, printed)

    def test_preview_text_matches_builder_rows(self):
        bp = _profile()
        txn = _txn()
        rows, cols, _ = receipt_layout.build_receipt_rows(txn, bp)
        lines = receipt_layout.render_text(txn, bp).split('\n')
        self.assertEqual(len(lines), len(rows))
        for line, r in zip(lines, rows):
            self.assertEqual(line.strip(), r['text'].strip())

    def test_sample_preview_renders_at_width(self):
        bp = _profile(paper_width='80mm', printer_font='A')
        text = receipt_layout.render_sample_text(bp)
        self.assertTrue(text)
        # dividers are full-width at 80mm/A = 48 cols
        self.assertIn('-' * 48, text)
