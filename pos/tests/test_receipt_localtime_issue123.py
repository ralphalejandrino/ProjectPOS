"""ISSUE-123 — printed documents must render LOCAL time, never the stored UTC.

Found from a real thermal receipt: OR-20260815-0308 was rung at 3:39 pm Manila
and printed "Date: 2026-08-15 07:39" — the UTC wall clock, 8 hours behind, on a
document carrying a BIR serial OR number.

Cause: with USE_TZ=True the stored datetimes are aware UTC, and a bare
``value.strftime(...)`` formats UTC. Django only converts to TIME_ZONE inside
templates, so each print path had to ask — and three did not (customer receipt
Date, Z report Period, X report Opened).

🔑 THE TRAP THESE TESTS ARE BUILT AROUND: a test written with a UTC instant whose
local rendering happens to look similar passes against the BROKEN code. Every
instant below is chosen so the correct Manila rendering differs from UTC in a way
no naive implementation can produce, and each test also asserts the UTC form is
ABSENT — so deleting the fix turns them red rather than merely un-green.
"""
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone as dj_tz
from escpos.printer import Dummy

import pos.receipt_service as rs
from pos import receipt_layout
from pos.models import BusinessProfile


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=dt_timezone.utc)


# The real receipt that exposed the bug: 07:39 UTC == 3:39 pm Manila.
REAL_RECEIPT_UTC = _utc(2026, 8, 15, 7, 39)
REAL_RECEIPT_LOCAL = '2026-08-15 15:39'
REAL_RECEIPT_UTC_TEXT = '2026-08-15 07:39'

# Crosses the date boundary: 17:00 UTC on Aug 14 is 01:00 on Aug 15 in Manila.
# A naive implementation gets the DAY wrong here, not just the hour.
BOUNDARY_UTC = _utc(2026, 8, 14, 17, 0)
BOUNDARY_LOCAL = '2026-08-15 01:00'
BOUNDARY_UTC_TEXT = '2026-08-14 17:00'


class _Money:
    def __init__(self, a):
        self.amount = a


class _Item:
    def __init__(self):
        self.quantity, self.unit_price = 1, Decimal('138.00')
        self.subtotal = _Money(Decimal('138.00'))
        self.item = type('I', (), {'name': 'Milk Tea - Wintermelon'})()
        self.variant_selections = type(
            'V', (), {'all': staticmethod(lambda: [])})()


class _Items:
    def select_related(self, *a):
        return self

    def all(self):
        return [_Item()]


def _txn(created_at):
    t = SimpleNamespace(
        transaction_no='OR-20260815-0308',
        created_at=created_at,
        cashier=SimpleNamespace(username='admin'),
        discount_amount=Decimal('0'), discount_type='none',
        vat_exempt=False, vat_amount=0,
        payment_method='gcash', gcash_reference='67589',
        maya_reference=None, customer_phone=None,
        total_amount=_Money(Decimal('138.00')),
        cash_received=None,
        items=_Items(),
        get_payment_method_display=lambda: 'GCash',
    )
    return t


def _zreport(**over):
    d = dict(
        currency='PHP', is_official=True,
        business_name='Demo Cafe', business_address='Baguio',
        business_tin='123-456-789', machine_identification_number='MIN-1',
        machine_serial_number='SN-1', pos_accreditation_number='ACC-1',
        pos_permit_number='PERM-1',
        z_counter=40, reset_counter=0, business_date=date(2026, 8, 14),
        started_at=REAL_RECEIPT_UTC, finalized_at=BOUNDARY_UTC,
        cashier=SimpleNamespace(username='admin'),
        first_or_number='OR-1', last_or_number='OR-9',
        voided_count=0, voided_or_numbers=[],
        gross_sales=Decimal('2336.00'), discount_total=Decimal('0'),
        sc_discount_total=Decimal('0'), pwd_discount_total=Decimal('0'),
        promo_discount_total=Decimal('0'), net_sales=Decimal('2336.00'),
        vatable_sales=Decimal('2085.71'), output_vat=Decimal('250.29'),
        vat_exempt_sales=Decimal('0'), zero_rated_sales=Decimal('0'),
        payment_breakdown={'cash': Decimal('2208.00')},
        opening_cash=Decimal('2000.00'), cash_collected=Decimal('2208.00'),
        cash_expected=Decimal('4208.00'), cash_counted=Decimal('3777.00'),
        over_short=Decimal('-431.00'), grand_total_sales=Decimal('2336.00'),
    )
    d.update(over)
    return SimpleNamespace(**d)


def _profile():
    bp = BusinessProfile.get_instance()
    bp.printer_mode = 'usb'
    bp.business_name = 'Demo Cafe'
    bp.currency = 'PHP'
    bp.paper_width = '58mm'
    bp.printer_font = 'A'
    bp.save()
    return bp


def _out(fn, *a):
    d = Dummy()
    with mock.patch.object(rs, 'File', return_value=d):
        fn(*a)
    return d.output.decode('ascii', 'replace')


@override_settings(TIME_ZONE='Asia/Manila', USE_TZ=True)
class FmtDtTests(TestCase):
    """The shared helper, in isolation."""

    def test_aware_utc_is_converted_to_manila(self):
        self.assertEqual(receipt_layout.fmt_dt(REAL_RECEIPT_UTC),
                         REAL_RECEIPT_LOCAL)

    def test_conversion_moves_the_DATE_not_just_the_clock(self):
        # 17:00 UTC Aug 14 -> 01:00 Aug 15. Catches an implementation that
        # adds 8 hours without rolling the day.
        self.assertEqual(receipt_layout.fmt_dt(BOUNDARY_UTC), BOUNDARY_LOCAL)

    def test_iso_string_is_parsed_and_converted(self):
        # The X report receives shift.opened_at.isoformat() over the API.
        self.assertEqual(receipt_layout.fmt_dt(REAL_RECEIPT_UTC.isoformat()),
                         REAL_RECEIPT_LOCAL)
        # ...and never leaks the ISO 'T' separator onto paper.
        self.assertNotIn('T', receipt_layout.fmt_dt(REAL_RECEIPT_UTC.isoformat()))

    # --- negative controls -------------------------------------------------
    def test_naive_datetime_passes_through_untouched(self):
        # The layout preview's sample txn is naive wall-clock. localtime()
        # raises ValueError on naive input, so this must NOT crash or shift.
        naive = datetime(2026, 5, 22, 14, 30)
        self.assertEqual(receipt_layout.fmt_dt(naive), '2026-05-22 14:30')

    def test_empty_and_none_render_blank_not_the_word_none(self):
        self.assertEqual(receipt_layout.fmt_dt(None), '')
        self.assertEqual(receipt_layout.fmt_dt(''), '')

    def test_unparseable_string_is_not_silently_dropped(self):
        self.assertIn('not-a-date', receipt_layout.fmt_dt('not-a-date'))

    def test_helper_actually_depends_on_TIME_ZONE(self):
        # Positive control on the mechanism itself: under a different zone the
        # SAME instant must render differently, proving conversion is real and
        # not a hardcoded +8.
        with override_settings(TIME_ZONE='UTC'):
            dj_tz.get_default_timezone.cache_clear()
            try:
                self.assertEqual(receipt_layout.fmt_dt(REAL_RECEIPT_UTC),
                                 REAL_RECEIPT_UTC_TEXT)
            finally:
                dj_tz.get_default_timezone.cache_clear()


@override_settings(TIME_ZONE='Asia/Manila', USE_TZ=True)
class CustomerReceiptLocalTimeTests(TestCase):
    def setUp(self):
        _profile()

    def test_receipt_prints_manila_time_not_utc(self):
        rows, _, _ = receipt_layout.build_receipt_rows(
            _txn(REAL_RECEIPT_UTC), BusinessProfile.get_instance())
        text = '\n'.join(r['text'] for r in rows)
        self.assertIn(f'Date: {REAL_RECEIPT_LOCAL}', text)
        self.assertNotIn(REAL_RECEIPT_UTC_TEXT, text)

    def test_printed_byte_stream_carries_the_local_time(self):
        # Not just the row list — what actually reaches the printer.
        out = _out(rs.print_receipt, _txn(REAL_RECEIPT_UTC))
        self.assertIn(REAL_RECEIPT_LOCAL, out)
        self.assertNotIn(REAL_RECEIPT_UTC_TEXT, out)

    def test_receipt_date_rolls_over_correctly(self):
        rows, _, _ = receipt_layout.build_receipt_rows(
            _txn(BOUNDARY_UTC), BusinessProfile.get_instance())
        text = '\n'.join(r['text'] for r in rows)
        self.assertIn(f'Date: {BOUNDARY_LOCAL}', text)
        self.assertNotIn(BOUNDARY_UTC_TEXT, text)


@override_settings(TIME_ZONE='Asia/Manila', USE_TZ=True)
class ZAndXReportLocalTimeTests(TestCase):
    def setUp(self):
        _profile()

    def test_z_report_period_is_local(self):
        out = _out(rs.print_z_report, _zreport())
        self.assertIn(f'Period: {REAL_RECEIPT_LOCAL} - {BOUNDARY_LOCAL}', out)
        self.assertNotIn(REAL_RECEIPT_UTC_TEXT, out)
        self.assertNotIn(BOUNDARY_UTC_TEXT, out)

    def test_x_report_opened_at_is_local_from_an_iso_string(self):
        data = {
            'cashier': 'admin',
            'opened_at': REAL_RECEIPT_UTC.isoformat(),
            'gross_sales': Decimal('1000.00'), 'void_total': Decimal('0'),
            'net_sales': Decimal('1000.00'), 'transaction_count': 7,
            'by_payment_method': [{'payment_method': 'cash', 'count': 7,
                                   'subtotal': Decimal('1000.00')}],
        }
        out = _out(rs.print_xreport_summary, data)
        self.assertIn(f'Opened: {REAL_RECEIPT_LOCAL}', out)
        self.assertNotIn(REAL_RECEIPT_UTC_TEXT, out)
        self.assertNotIn('T07:39', out)

    def test_x_report_still_prints_when_opened_at_is_a_datetime(self):
        # Negative control: the other caller passes a real datetime, not ISO.
        data = {
            'cashier': 'admin', 'opened_at': REAL_RECEIPT_UTC,
            'gross_sales': Decimal('1000.00'), 'void_total': Decimal('0'),
            'net_sales': Decimal('1000.00'), 'transaction_count': 7,
            'by_payment_method': [{'payment_method': 'cash', 'count': 7,
                                   'subtotal': Decimal('1000.00')}],
        }
        out = _out(rs.print_xreport_summary, data)
        self.assertIn(f'Opened: {REAL_RECEIPT_LOCAL}', out)
