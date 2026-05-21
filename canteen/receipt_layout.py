"""FEATURE-040: single source of truth for receipt layout.

Both the ESC/POS printer (canteen/receipt_service.py) and the on-screen preview
render from the SAME row list produced here, so screen and paper are structurally
identical by construction — this is what makes FLAG-064 parity provable rather
than hand-maintained in two languages.

A "row" is a dict:
    {'text': str, 'align': 'left'|'center', 'bold': bool, 'title': bool}
Money / two-column rows have `text` pre-padded to the column width (label, spaces,
value) so a monospace screen shows the same alignment the printer produces.

All rates, labels, currency and identity come from BusinessProfile — never
hardcoded (hard rule; ISSUE-079).
"""
from decimal import Decimal, ROUND_HALF_UP

from .utils.currency import format_currency

# ESC/POS printable columns per paper width + font. Font A is 12 dots wide,
# Font B is 9 dots; 58mm carriage = 384 dots, 80mm = 576 dots. (Corrected in
# FEATURE-040 — the prior table was wrong for every combo.) Font B values are
# the theoretical maxima and may want on-device tuning.
WIDTH_FONT_COLS = {
    ('58mm', 'A'): 32,   # 384 / 12
    ('58mm', 'B'): 42,   # 384 / 9
    ('80mm', 'A'): 48,   # 576 / 12
    ('80mm', 'B'): 64,   # 576 / 9
}
DEFAULT_COLS = 32  # 58mm / Font A fallback


def receipt_cols(profile):
    """Printable column count for the profile's paper width + font."""
    if not profile:
        return DEFAULT_COLS
    return WIDTH_FONT_COLS.get((profile.paper_width, profile.printer_font), DEFAULT_COLS)


def _truncate(text, max_len):
    return text[:max_len - 3] + '...' if len(text) > max_len else text


def _row(text, align='left', bold=False, title=False):
    return {'text': text, 'align': align, 'bold': bold, 'title': title}


def _money_row(label, value, cols, bold=False):
    """Label left, value right-aligned within `cols` (min 1 space between)."""
    pad = cols - len(label) - len(value)
    return _row(label + ' ' * max(pad, 1) + value, bold=bold)


def _item_rows(item, cols, money):
    """Item line(s): 'name  qty x price        subtotal', wrapping the qty/price
    to an indented second line when the single line would overflow. Variant
    selections are indented underneath."""
    rows = []
    qty_price = f'{item.quantity} x {float(item.unit_price):.2f}'
    subtotal_amt = item.subtotal.amount if hasattr(item.subtotal, 'amount') else item.subtotal
    subtotal = f'{float(subtotal_amt):.2f}'
    name = item.item.name if item.item else 'Item'

    single = f'{name} {qty_price}'
    if len(single) + 1 + len(subtotal) <= cols:
        pad = cols - len(single) - len(subtotal)
        rows.append(_row(single + ' ' * max(pad, 1) + subtotal))
    else:
        name_trunc = _truncate(name, cols - len(subtotal) - 1)
        pad = cols - len(name_trunc) - len(subtotal)
        rows.append(_row(name_trunc + ' ' * max(pad, 1) + subtotal))
        rows.append(_row(f'  {qty_price}'))

    for vs in item.variant_selections.all():
        mod = float(vs.price_modifier or 0)
        if mod > 0:
            modifier_str = ' +' + money(mod)
        elif mod < 0:
            modifier_str = ' ' + money(mod)  # native '-' (ISSUE-074)
        else:
            modifier_str = ''
        rows.append(_row(f'  {vs.group_name}: {vs.option_name}{modifier_str}'))
    return rows


def build_receipt_rows(transaction, profile, ascii_currency=False):
    """Build the ordered row list for a receipt. Returns (rows, cols, ccode).

    Pure layout — no I/O, no ESC/POS. Mirrors the legacy print_receipt body so
    the printed output is unchanged except for the corrected width, font and the
    (new) logo, which the printer consumer adds around these rows.

    ISSUE-114: ``ascii_currency`` controls the money token. The printer passes
    True (PC437 has no ₱ glyph → ISO-prefix form like "PHP 120.00"); the on-screen
    preview passes False so the browser shows the Unicode ₱. This screen-vs-paper
    symbol divergence is INTENTIONAL and acceptable (the glyph is hardware-
    unprintable, not a FLAG-064 parity violation) — do not "fix" it back.
    """
    cols = receipt_cols(profile)
    ccode = profile.currency if profile else 'PHP'

    def money(v):
        return format_currency(v, ccode, ascii_only=ascii_currency)

    rows = []

    # --- Header: identity (centered) ---
    rows.append(_row((profile.business_name if profile else 'POS'),
                     align='center', bold=True, title=True))
    if profile and profile.tagline:
        rows.append(_row(profile.tagline, align='center'))
    if profile and profile.address:
        for line in profile.address.strip().splitlines():
            if line.strip():
                rows.append(_row(line.strip(), align='center'))
    if profile and profile.tin:
        rows.append(_row(f'TIN: {profile.tin}', align='center'))
    min_no = getattr(profile, 'machine_identification_number', '') if profile else ''
    serial_no = getattr(profile, 'machine_serial_number', '') if profile else ''
    if min_no:
        rows.append(_row(f'MIN: {min_no}', align='center'))
    if serial_no:
        rows.append(_row(f'Serial: {serial_no}', align='center'))

    rows.append(_row('-' * cols))
    if profile and profile.receipt_header:
        rows.append(_row(profile.receipt_header, align='center'))

    # --- Transaction info (left) ---
    rows.append(_row(f'Receipt #: {transaction.transaction_no}'))
    rows.append(_row(f'Date: {transaction.created_at.strftime("%Y-%m-%d %H:%M")}'))
    if transaction.cashier:
        rows.append(_row(f'Cashier: {transaction.cashier.username}'))
    rows.append(_row('-' * cols))

    # --- Items ---
    subtotal_sum = 0.0
    for item in transaction.items.select_related('item').all():
        sub_amt = item.subtotal.amount if hasattr(item.subtotal, 'amount') else item.subtotal
        subtotal_sum += float(sub_amt)
        rows.extend(_item_rows(item, cols, money))
    rows.append(_row('-' * cols))

    # --- Totals ---
    total = float(transaction.total_amount.amount) if hasattr(
        transaction.total_amount, 'amount') else float(transaction.total_amount)
    is_vat_exempt = getattr(transaction, 'vat_exempt', False)
    stored_vat_amount = float(getattr(transaction, 'vat_amount', 0) or 0)
    disc_type = getattr(transaction, 'discount_type', '')

    vat_inclusive = bool(profile and getattr(profile, 'vat_inclusive', False))
    subtotal_label = 'Subtotal (VAT-inc):' if vat_inclusive else 'Subtotal:'
    rows.append(_money_row(subtotal_label, money(subtotal_sum), cols))

    if is_vat_exempt and stored_vat_amount > 0:
        vat_pct = int(profile.vat_rate) if profile and hasattr(profile, 'vat_rate') else 12
        rows.append(_money_row(f'VAT ({vat_pct}%) Removed:',
                               money(-stored_vat_amount), cols))

    discount = float(transaction.discount_amount) if transaction.discount_amount else 0.0
    if discount > 0:
        if disc_type == 'sc':
            rate = int(profile.sc_discount_rate) if profile and hasattr(profile, 'sc_discount_rate') else 20
            disc_label = f'SC Discount ({rate}%):'
        elif disc_type == 'pwd':
            rate = int(profile.pwd_discount_rate) if profile and hasattr(profile, 'pwd_discount_rate') else 20
            disc_label = f'PWD Discount ({rate}%):'
        elif disc_type == 'promo':
            disc_label = 'Promo Discount:'
        else:
            disc_label = 'Discount:'
        rows.append(_money_row(disc_label, money(-discount), cols))
        id_number = getattr(transaction, 'discount_id_number', '')
        if id_number and disc_type in ('sc', 'pwd'):
            id_prefix = 'SC ID: ' if disc_type == 'sc' else 'PWD ID: '
            rows.append(_row(f'{id_prefix}{id_number}'))

    rows.append(_money_row('TOTAL:', money(total), cols, bold=True))

    if profile and profile.vat_enabled and not is_vat_exempt:
        vat_rate = float(profile.vat_rate)
        vat_amount = total * vat_rate / (100 + vat_rate)
        rows.append(_money_row(f'Incl. VAT ({vat_rate:.0f}%):',
                               money(vat_amount), cols))

    if is_vat_exempt:
        ra_ref = 'RA 9994' if disc_type == 'sc' else 'RA 10754'
        rows.append(_row('VAT-EXEMPT TRANSACTION', align='center', bold=True))
        rows.append(_row(f'({ra_ref})', align='center'))

    rows.append(_row(f'Payment: {transaction.get_payment_method_display()}'))
    if transaction.payment_method == 'cash' and transaction.cash_received:
        cash_raw = transaction.cash_received.amount if hasattr(
            transaction.cash_received, 'amount') else transaction.cash_received
        cash_dec = Decimal(str(cash_raw))
        change_dec = (cash_dec - Decimal(str(total))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP)
        rows.append(_row(
            f'Cash: {money(cash_dec)}  '
            f'Change: {money(change_dec)}'))
    elif transaction.payment_method in ('gcash', 'maya'):
        ref = transaction.gcash_reference or transaction.maya_reference or 'N/A'
        rows.append(_row(f'Ref#: {ref}'))
        if transaction.customer_phone:
            rows.append(_row(f'Phone: {transaction.customer_phone}'))

    # --- Footer ---
    rows.append(_row('-' * cols))
    rows.append(_row(profile.receipt_footer if (profile and profile.receipt_footer)
                     else 'Thank you!', align='center'))
    return rows, cols, ccode


def render_text(transaction, profile):
    """Plain-text receipt for the on-screen preview — centered lines centered
    within the column width, so the monospace preview matches the paper."""
    rows, cols, _ = build_receipt_rows(transaction, profile)
    out = []
    for r in rows:
        text = r['text']
        out.append(text.center(cols) if r['align'] == 'center' else text)
    return '\n'.join(out)


def render_sample_text(profile):
    """Faithful preview text for a representative sale, at the profile's current
    width/font/labels/currency. Used by the Settings on-screen preview — same
    builder as the printer, so what you see is what prints."""
    from datetime import datetime

    class _Money:
        def __init__(self, a):
            self.amount = a

    class _VS:
        def __init__(self, g, o, m):
            self.group_name, self.option_name, self.price_modifier = g, o, m

    class _Variants:
        def __init__(self, vs):
            self._vs = vs

        def all(self):
            return self._vs

    class _SampleItem:
        def __init__(self, name, qty, price, sub, vs=()):
            self.quantity, self.unit_price = qty, price
            self.subtotal = _Money(sub)
            self.item = type('I', (), {'name': name})()
            self.variant_selections = _Variants(list(vs))

    class _Items:
        def select_related(self, *a):
            return self

        def all(self):
            return [
                _SampleItem('Brewed Coffee', 2, 60.0, 120.0,
                            vs=[_VS('Size', 'Large', 10.0)]),
                _SampleItem('Ham & Cheese Sandwich Special', 1, 95.0, 95.0),
            ]

    class _SampleTxn:
        transaction_no = 'OR-000123'
        created_at = datetime(2026, 5, 22, 14, 30)
        cashier = type('C', (), {'username': 'cashier1'})()
        discount_amount = 0
        discount_type = 'none'
        vat_exempt = False
        vat_amount = 0
        payment_method = 'cash'
        gcash_reference = None
        maya_reference = None
        customer_phone = None
        total_amount = _Money(225.0)
        cash_received = _Money(500.0)
        items = _Items()

        def get_payment_method_display(self):
            return 'Cash'

    return render_text(_SampleTxn(), profile)
