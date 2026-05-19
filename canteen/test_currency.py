"""ISSUE-079 — currency formatter unit tests."""

from decimal import Decimal
from django.test import SimpleTestCase

from canteen.utils.currency import currency_symbol, format_currency


class FormatCurrencyTests(SimpleTestCase):
    def test_php_default(self):
        self.assertEqual(format_currency(Decimal('1234.5'), 'PHP'), '₱1,234.50')

    def test_usd(self):
        self.assertEqual(format_currency('99.9', 'USD'), '$99.90')

    def test_eur(self):
        self.assertEqual(format_currency(1, 'EUR'), '€1.00')

    def test_gbp(self):
        self.assertEqual(format_currency('0', 'GBP'), '£0.00')

    def test_jpy_zero_decimals(self):
        self.assertEqual(format_currency(1234, 'JPY'), '¥1,234')
        self.assertEqual(format_currency(Decimal('1234.50'), 'JPY'), '¥1,235')

    def test_sgd_fallback_iso_prefix(self):
        self.assertEqual(format_currency(Decimal('1234.50'), 'SGD'), 'SGD 1,234.50')

    def test_negative_sign_before_symbol(self):
        self.assertEqual(format_currency(Decimal('-5'), 'PHP'), '-₱5.00')
        self.assertEqual(format_currency(Decimal('-12.34'), 'USD'), '-$12.34')

    def test_lowercase_code_normalized(self):
        self.assertEqual(format_currency('1', 'usd'), '$1.00')

    def test_none_defaults_to_php(self):
        self.assertEqual(format_currency('10', None), '₱10.00')


class CurrencySymbolTests(SimpleTestCase):
    def test_known_symbols(self):
        self.assertEqual(currency_symbol('PHP'), '₱')
        self.assertEqual(currency_symbol('JPY'), '¥')

    def test_unknown_returns_empty(self):
        self.assertEqual(currency_symbol('SGD'), '')
