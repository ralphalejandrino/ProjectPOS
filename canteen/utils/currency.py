"""Currency formatting (ISSUE-079).

Single source of truth for currency display across receipts, errors, and
admin __str__ methods. Symbol resolved from BusinessProfile.currency
(ISO 4217); unknown codes fall back to an ISO prefix (e.g. ``SGD 1,234.50``).
"""

from decimal import Decimal, ROUND_HALF_UP

_SYMBOLS = {
    'PHP': '₱',
    'USD': '$',
    'EUR': '€',
    'GBP': '£',
    'JPY': '¥',
}


def currency_symbol(code):
    """Return the display symbol for ``code`` or empty string when unknown."""
    return _SYMBOLS.get((code or 'PHP').upper(), '')


def format_currency(amount, code='PHP'):
    """Format ``amount`` for display in the given currency.

    JPY uses 0 decimals; everything else uses 2. Negative values keep
    the sign before the symbol (``-₱5.00``). Unknown ISO codes get an
    ISO prefix instead of a symbol (``SGD 1,234.50``).
    """
    code = (code or 'PHP').upper()
    decimals = 0 if code == 'JPY' else 2
    try:
        value = Decimal(str(amount))
    except Exception:
        value = Decimal('0')
    sign = '-' if value < 0 else ''
    quant = Decimal('1') if decimals == 0 else Decimal('0.01')
    formatted = f"{abs(value).quantize(quant, rounding=ROUND_HALF_UP):,.{decimals}f}"
    symbol = _SYMBOLS.get(code)
    if symbol:
        return f"{sign}{symbol}{formatted}"
    return f"{sign}{code} {formatted}"
