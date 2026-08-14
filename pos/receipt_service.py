from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from escpos.printer import File, Network
from .models import BusinessProfile
from .utils.currency import format_currency
from .receipt_layout import build_receipt_rows, receipt_cols
import logging

# ISSUE-095 / BUG-004: USB printer device auto-detect.
# The kernel assigns the printer's device node at enumeration time and it is
# NOT stable across restarts — a USB re-enumeration can bump the printer from
# lp0 to lp2. So detection GLOBS for whatever lp* node is actually present
# rather than trusting a fixed list (the old list missed lp2 and printing died).
# The box has exactly one printer, so "the lp node that exists" is unambiguous.
# The fixed candidates remain only as a last-resort fallback.
_USB_DEVICE_GLOBS = ['/dev/usb/lp*', '/dev/lp*']
_USB_DEVICE_CANDIDATES = ['/dev/usb/lp1', '/dev/usb/lp0', '/dev/lp0', '/dev/lp1']
_cached_usb_path = None

def _usable_printer_device(p):
    """True when p is a present, writable device node."""
    import os
    return os.path.exists(p) and os.access(p, os.W_OK)

def _detect_usb_device_paths():
    """All present lp* device nodes, deterministically (sorted) ordered.

    /dev/usb/lp* first, then /dev/lp* — within each glob the paths are sorted
    so the choice is stable when more than one node is present.
    """
    import glob
    paths = []
    for pattern in _USB_DEVICE_GLOBS:
        paths.extend(sorted(glob.glob(pattern)))
    return paths

def _get_usb_device_path():
    """First usable USB printer device path, cached; None if none work.

    BUG-004: genuinely detect by globbing the real /dev/usb/lp* + /dev/lp*
    nodes first (the printer can land on lp2 after a re-enumeration, which the
    old fixed candidate list missed), then fall back to the known fixed
    candidates in case the glob finds nothing but a known path exists.
    """
    global _cached_usb_path
    if _cached_usb_path is not None:
        return _cached_usb_path
    # 1. Real detection: whatever lp* node is actually present.
    for p in _detect_usb_device_paths():
        if _usable_printer_device(p):
            logging.getLogger(__name__).info("Receipt printer USB device detected at: %s", p)
            _cached_usb_path = p
            return p
    # 2. Secondary fallback: known fixed paths (glob found nothing usable).
    for p in _USB_DEVICE_CANDIDATES:
        if _usable_printer_device(p):
            logging.getLogger(__name__).info("Receipt printer USB device detected at: %s", p)
            _cached_usb_path = p
            return p
    logging.getLogger(__name__).warning(
        "No USB printer device found. Globbed: %s; tried candidates: %s",
        _USB_DEVICE_GLOBS, _USB_DEVICE_CANDIDATES)
    return None
logger = logging.getLogger(__name__)

# Column count + the corrected width/font table now live in receipt_layout.py
# (single source of truth shared with the on-screen preview — FEATURE-040).

# Full printable carriage width in dots per paper size (58mm = 384, 80mm = 576).
_PAPER_DOTS = {'58mm': 384, '80mm': 576}
# FEATURE-040 follow-up: the logo printed at full carriage width (too big).
# Render it at this fraction of the paper width, centered. Single tuning knob —
# bump it here (e.g. 0.50–0.65) to make the printed logo smaller/larger.
LOGO_PAPER_WIDTH_FRACTION = 0.58  # ~222 dots @58mm, ~334 dots @80mm

def _logo_target_dots(profile):
    """Target logo width in dots: a fraction of the paper's printable width."""
    full = _PAPER_DOTS.get(getattr(profile, 'paper_width', '58mm'), 384)
    return int(full * LOGO_PAPER_WIDTH_FRACTION)

def _is_printer_enabled(profile):
    """True when a transport is configured (USB or network)."""
    return bool(profile) and profile.printer_mode in ('usb', 'network')

def _get_transport(profile):
    """ESC/POS transport for the profile's mode, or None when disabled."""
    if profile.printer_mode == 'usb':
        return File(_get_usb_device_path() or '/dev/usb/lp1')
    elif profile.printer_mode == 'network':
        return Network(profile.printer_ip, port=profile.printer_port or 9100)
    return None

def _receipt_cols(profile):
    """Printable column count for the profile's paper width + font."""
    return receipt_cols(profile)

def _escpos_font(profile):
    """Map BusinessProfile printer_font (A/B) to escpos font ('a'/'b')."""
    return 'a' if profile.printer_font == 'A' else 'b'

def _pset(p, profile, **kwargs):
    """Wrapper for p.set() that ALWAYS re-asserts the font.

    FEATURE-040 fix: escpos's double_height/double_width path emits ESC ! 0
    (TXT_NORMAL), whose bit 0 resets the printer to Font A — silently undoing an
    earlier set(font=...). Since escpos emits the size (ESC !) before the font
    (ESC M) within one set() call, injecting font= on every call re-selects the
    chosen font after the reset, making A/B genuinely distinct on the body."""
    kwargs.setdefault('font', _escpos_font(profile))
    p.set(**kwargs)

def _print_logo(p, profile):
    """Rasterize and emit the business logo centered, scaled to paper width
    (GS v 0 bit-image). Non-fatal — a logo problem must never block the sale."""
    logo = getattr(profile, 'logo', None)
    if not logo:
        return
    try:
        path = logo.path
    except (ValueError, AttributeError):
        return
    import os
    if not os.path.exists(path):
        return
    try:
        from PIL import Image
        target_dots = _logo_target_dots(profile)  # ~58% of paper width, centered
        img = Image.open(path).convert('L')
        if img.width != target_dots:
            ratio = target_dots / img.width
            img = img.resize((target_dots, max(1, int(img.height * ratio))))
        # Centering is done via ESC a (align='center') below — it is honored for
        # raster images on ESC/POS hardware. The escpos center= flag needs a
        # media-width profile we don't configure, so we don't use it.
        p.set(align='center')
        p.image(img, impl='bitImageRaster')
        p.text('\n')
    except Exception as e:
        logger.warning(f'Logo print skipped (non-fatal): {e}')

def print_receipt(transaction):
    """ESC/POS receipt print. Returns status dict. Never raises."""
    try:
        profile = BusinessProfile.objects.first()
        if not _is_printer_enabled(profile):
            return {'success': False, 'message': 'Printer not configured. Set up Business Profile in Settings.'}

        p = _get_transport(profile)
        _pset(p, profile, align='left')
        try:
            # Logo (centered raster, scaled to paper width) above the header.
            _print_logo(p, profile)

            # Body: single source of truth shared with the on-screen preview.
            # ascii_currency=True — PC437 can't print ₱, so the paper uses the
            # ISO-prefix form ("PHP 120.00"); the screen preview keeps ₱ (ISSUE-114).
            rows, cols, ccode = build_receipt_rows(transaction, profile, ascii_currency=True)
            for r in rows:
                if r['title']:
                    # Business name — emphasized, double size.
                    _pset(p, profile, align=r['align'], bold=True,
                          double_height=True, double_width=True)
                    p.text(r['text'] + '\n')
                    # Reset to NORMAL size for the body. normal_textsize=True is
                    # required — passing double_*=False does NOT reset size (the
                    # old code's bug that left the whole body double-sized).
                    _pset(p, profile, normal_textsize=True, align='left', bold=False)
                else:
                    _pset(p, profile, align=r['align'], bold=r['bold'])
                    p.text(r['text'] + '\n')

            p.cut()
        finally:
            p.close()
        return {'success': True, 'message': 'Receipt printed.'}

    except Exception as e:
        logger.warning(f'Receipt print failed (non-fatal): {e}')
        return {'success': False, 'message': 'Printer error. Check connection.'}


# ── ISSUE-121-FU-H: shared thermal layout primitives ──────────────────────
# One set of formatting helpers so the X, Z, and Weekly thermal reports share
# the SAME divider style, label/value column spacing, and centered section
# titles. They operate on the printable column count (_receipt_cols), so they
# honor each BusinessProfile's paper width. Bold/double-height emphasis stays
# per-builder (ESC/POS state) — these unify the text geometry.
def _thermal_rule(width, ch='-'):
    """A full-width divider line ('-' for sections, '=' for banners)."""
    return ch * width

def _thermal_kv(label, val, width):
    """A label/value row: label left, value right, space-padded to `width`."""
    label, val = str(label), str(val)
    pad = width - len(label) - len(val)
    return label + ' ' * max(pad, 1) + val

def _thermal_center(text, width):
    """Center `text` within `width` (clamped). Lets the pure-line Weekly
    builder center section titles the same way the X/Z ESC-centering does."""
    text = str(text)
    return text[:width] if len(text) >= width else text.center(width)


def print_z_report(z_report):
    """Print an immutable ZReport via ESC/POS thermal (58mm, PC437).

    Reads ONLY from the frozen ``z_report`` instance — never from the
    live BusinessProfile, never from live PosTransaction rows. This is
    the FLAG-058 parity guarantee: the printed Z and the HTML Z are
    rendered from the same immutable snapshot.

    Returns True on success, False on any printer error (logged, never
    raised — mirrors print_receipt's non-fatal contract).
    """
    try:
        profile = BusinessProfile.objects.first()
        if not _is_printer_enabled(profile):
            logger.warning('Z-report print skipped: printer not configured.')
            return False

        # Currency is the ONLY value still allowed from BP-derived state,
        # and even that is taken off the frozen ZReport snapshot.
        ccode = z_report.currency or 'PHP'
        # Paper width / font are device config (not frozen content), so they
        # come from the live profile — FLAG-058 parity is about Z *content*.
        RECEIPT_WIDTH = _receipt_cols(profile)
        p = _get_transport(profile)
        _pset(p, profile, align='left')

        def rrow(label, val):
            return _thermal_kv(label, val, RECEIPT_WIDTH) + '\n'

        def money(val):
            return format_currency(val, ccode, ascii_only=True)

        official = z_report.is_official

        try:
            # --- ISSUE-105: UNOFFICIAL top banner ---
            if not official:
                _pset(p, profile, align='center', bold=True)
                p.text(_thermal_rule(RECEIPT_WIDTH, '=') + '\n')
                _pset(p, profile, align='center', bold=True,
                      double_height=True, double_width=False)
                p.text('*** UNOFFICIAL ***\n')
                p.text('NOT FOR BIR SUBMISSION\n')
                _pset(p, profile, normal_textsize=True, align='center', bold=True)
                p.text(_thermal_rule(RECEIPT_WIDTH, '=') + '\n')
                _pset(p, profile, align='left', bold=False)

            # --- Header (frozen identity) ---
            _pset(p, profile, align='center', bold=True, double_height=True, double_width=True)
            p.text((z_report.business_name or 'Z-REPORT') + '\n')
            _pset(p, profile, normal_textsize=True, align='center', bold=False)
            if z_report.business_address:
                for line in z_report.business_address.strip().splitlines():
                    line = line.strip()
                    if line:
                        p.text(line + '\n')
            if z_report.business_tin:
                p.text(f'TIN: {z_report.business_tin}\n')
            # Identity rows only on official Zs — printing blank labels on
            # an unofficial Z looks like a redaction (ISSUE-105).
            if official:
                if z_report.machine_identification_number:
                    p.text(f'MIN: {z_report.machine_identification_number}\n')
                if z_report.machine_serial_number:
                    p.text(f'Serial: {z_report.machine_serial_number}\n')
                if z_report.pos_accreditation_number:
                    p.text(f'Accreditation: {z_report.pos_accreditation_number}\n')
                if z_report.pos_permit_number:
                    p.text(f'Permit: {z_report.pos_permit_number}\n')

            # --- Z block ---
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            _pset(p, profile, align='center', bold=True)
            p.text('Z REPORT\n')
            _pset(p, profile, align='left', bold=False)
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            p.text(rrow(f'Z #: {z_report.z_counter}',
                        f'Reset: {z_report.reset_counter}'))
            p.text(f'Business Date: {z_report.business_date}\n')
            started = z_report.started_at.strftime('%Y-%m-%d %H:%M')
            finalized = z_report.finalized_at.strftime('%Y-%m-%d %H:%M')
            p.text(f'Period: {started} - {finalized}\n')
            p.text(f'Cashier: {z_report.cashier.username}\n')
            # ISSUE-094: explicit shift attribution. Opener comes from the
            # Shift record (shift.cashier); closer is the finalizing cashier
            # on the frozen ZReport. Accessed defensively so a Z snapshot
            # without a live shift link still prints.
            _shift = getattr(z_report, 'shift', None)
            _opener = getattr(getattr(_shift, 'cashier', None), 'username', None)
            if _opener:
                p.text(f'Opened by: {_opener}\n')
            if z_report.cashier:
                p.text(f'Closed by: {z_report.cashier.username}\n')
            p.text(rrow(f'From: {z_report.first_or_number or "-"}',
                        f'To: {z_report.last_or_number or "-"}'))
            p.text(f'Voided: {z_report.voided_count}\n')
            for orn in (z_report.voided_or_numbers or []):
                p.text(f'  VOID {orn}\n')

            # --- Sales summary ---
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            p.text(rrow('Gross Sales:', money(z_report.gross_sales)))
            p.text(rrow('Less Discounts:', money(-z_report.discount_total)))
            if z_report.sc_discount_total:
                p.text(rrow('  SC:', money(-z_report.sc_discount_total)))
            if z_report.pwd_discount_total:
                p.text(rrow('  PWD:', money(-z_report.pwd_discount_total)))
            if z_report.promo_discount_total:
                p.text(rrow('  Promo:', money(-z_report.promo_discount_total)))
            _pset(p, profile, bold=True)
            p.text(rrow('Net Sales:', money(z_report.net_sales)))
            _pset(p, profile, bold=False)
            p.text(rrow('Vatable Sales:', money(z_report.vatable_sales)))
            p.text(rrow('Output VAT:', money(z_report.output_vat)))
            p.text(rrow('VAT-Exempt Sales:', money(z_report.vat_exempt_sales)))
            p.text(rrow('Zero-Rated Sales:', money(z_report.zero_rated_sales)))

            # --- Payments ---
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            p.text('PAYMENTS:\n')
            labels = {'cash': 'Cash', 'gcash': 'GCash',
                      'maya': 'Maya', 'card': 'Card'}
            total_payments = Decimal('0')
            for method, raw in (z_report.payment_breakdown or {}).items():
                amt = Decimal(str(raw or 0))
                if amt == 0:
                    continue
                total_payments += amt
                p.text(rrow(f'  {labels.get(method, method.title())}:',
                            money(amt)))
            _pset(p, profile, bold=True)
            p.text(rrow('Total Payments:', money(total_payments)))
            _pset(p, profile, bold=False)

            # --- Cash reconciliation ---
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            p.text(rrow('Opening Cash:', money(z_report.opening_cash)))
            p.text(rrow('Cash Collected:', money(z_report.cash_collected)))
            # FEATURE-059: print non-sale drawer movements so a discrepancy is
            # explained on the slip itself rather than argued over later.
            # getattr keeps this safe against Z rows written before the fields
            # existed.
            _paid_in = getattr(z_report, 'cash_paid_in', 0) or 0
            _paid_out = getattr(z_report, 'cash_paid_out', 0) or 0
            if _paid_in:
                p.text(rrow('Cash Added:', money(_paid_in)))
            if _paid_out:
                p.text(rrow('Cash Paid Out:', '-' + money(_paid_out)))
            p.text(rrow('Cash Expected:', money(z_report.cash_expected)))
            if z_report.cash_counted is not None:
                p.text(rrow('Cash Counted:', money(z_report.cash_counted)))
            if z_report.over_short is not None:
                os_val = money(z_report.over_short)
                if z_report.over_short >= 0:
                    os_val = '+' + os_val
                p.text(rrow('Over/Short:', os_val))
            # FEATURE-059: receivables opened/closed this shift. Shown apart
            # from the cash block because credit is owed money, not drawer
            # money — folding it in is exactly the confusion this ticket fixes.
            _cr_ext = getattr(z_report, 'credit_extended', 0) or 0
            _cr_set = getattr(z_report, 'credit_settled', 0) or 0
            if _cr_ext or _cr_set:
                p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
                if _cr_ext:
                    p.text(rrow('Credit Extended:', money(_cr_ext)))
                if _cr_set:
                    p.text(rrow('Credit Settled:', money(_cr_set)))

            # --- Stock movement (FEATURE-008) ---
            # Read live from the IngredientLog ledger; skip the block entirely
            # when nothing moved. Local import avoids the services<->receipt
            # circular import at module load.
            try:
                from .services import stock_movements_for_shift
                movements = stock_movements_for_shift(
                    getattr(z_report, 'shift', None)
                )
            except Exception:
                movements = []
            if movements:
                p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
                _pset(p, profile, align='center', bold=True)
                p.text('STOCK MOVEMENT\n')
                _pset(p, profile, align='left', bold=False)
                for mv in movements:
                    qty_str = ('%.4f' % mv['sold']).rstrip('0').rstrip('.') or '0'
                    name = mv['ingredient_name']
                    max_name = RECEIPT_WIDTH - len(qty_str) - 1
                    if max_name > 0 and len(name) > max_name:
                        name = name[:max_name]
                    p.text(rrow(name, qty_str))

            # --- Footer ---
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            _pset(p, profile, bold=True)
            p.text(rrow('Grand Total Sales:',
                        money(z_report.grand_total_sales)))
            _pset(p, profile, bold=False, align='center')
            p.text(f'Generated {finalized}\n')
            if not official:
                _pset(p, profile, align='center', bold=True)
                p.text(_thermal_rule(RECEIPT_WIDTH, '=') + '\n')
                p.text('*** UNOFFICIAL Z REPORT ***\n')
                p.text(_thermal_rule(RECEIPT_WIDTH, '=') + '\n')
                _pset(p, profile, align='center', bold=False)
            p.cut()
        finally:
            p.close()
        return True
    except Exception as e:
        logger.warning(f'Z-report print failed (non-fatal): {e}')
        return False


def print_xreport_summary(data):
    """Print X-report summary via ESC/POS. Returns status dict. Never raises."""
    try:
        profile = BusinessProfile.objects.first()
        if not _is_printer_enabled(profile):
            return {'success': False, 'message': 'Printer not configured.'}
        ccode = profile.currency if profile else 'PHP'
        RECEIPT_WIDTH = _receipt_cols(profile)
        p = _get_transport(profile)
        _pset(p, profile, align='left')
        try:
            _pset(p, profile, align='center', bold=True, double_height=True, double_width=True)
            p.text((profile.business_name if profile else 'X-REPORT') + '\n')
            _pset(p, profile, normal_textsize=True, align='center', bold=False)
            if profile and profile.tagline:
                p.text(profile.tagline + '\n')
            if profile and profile.receipt_header:
                p.text(profile.receipt_header + '\n')
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            _pset(p, profile, align='center', bold=True)
            p.text('X-REPORT - SHIFT SUMMARY\n')
            _pset(p, profile, align='left', bold=False)
            if data.get('cashier'):
                p.text(f"Cashier: {data['cashier']}\n")
                # ISSUE-094: an X-report is a mid-shift reading of an OPEN
                # shift, so only the opener is known. "Closed by" is printed
                # on the Z-report at finalization, not here.
                p.text(f"Opened by: {data['cashier']}\n")
            if data.get('opened_at'):
                p.text(f"Opened: {str(data['opened_at'])[:16]}\n")
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')

            def rrow(label, val):
                return _thermal_kv(label, val, RECEIPT_WIDTH) + '\n'

            p.text(rrow('Gross Sales:', format_currency(data.get('gross_sales', 0), ccode, ascii_only=True)))
            p.text(rrow('Voids:', format_currency(data.get('void_total', 0), ccode, ascii_only=True)))
            _pset(p, profile, bold=True)
            p.text(rrow('Net Sales:', format_currency(data.get('net_sales', 0), ccode, ascii_only=True)))
            _pset(p, profile, bold=False)
            p.text(rrow('Transactions:', str(data.get('transaction_count', 0))))
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            for row in data.get('by_payment_method', []):
                method = {
                    'cash': 'Cash', 'gcash': 'GCash', 'maya': 'Maya',
                    'card': 'Card', 'credit': 'Credit/Unpaid',  # FEATURE-059
                }.get(
                    row.get('payment_method', ''), row.get('payment_method', 'Other'))
                p.text(rrow(f"  {method} ({row.get('count', 0)}):",
                            format_currency(row.get('subtotal', 0), ccode, ascii_only=True)))
            p.text(_thermal_rule(RECEIPT_WIDTH) + '\n')
            _pset(p, profile, align='center')
            if profile and profile.receipt_footer:
                p.text(profile.receipt_footer + '\n')
            p.cut()
        finally:
            p.close()
        return {'success': True, 'message': 'X-report printed.'}
    except Exception as e:
        logger.warning(f'X-report print failed (non-fatal): {e}')
        return {'success': False, 'message': 'Printer error. Check connection.'}


def build_weekly_report_lines(payload, profile):
    """ISSUE-121-FU-D: build the weekly report as thermal text lines.

    Pure (no hardware) so it is unit-testable and so print_weekly_report stays
    a thin emit loop. Single column, no wide tables — every figure is a
    label/value row padded to the paper width. ``payload`` is the dict from
    views._weekly_payload; ``profile`` supplies paper width + currency.

    ISSUE-121-FU-H: shares the divider / KV-row / centered-title geometry with
    the X and Z thermal builders via the module-level _thermal_* helpers. Since
    print_weekly_report emits these lines left-aligned, section titles are
    pre-centered with _thermal_center so they read like the ESC-centered X/Z
    titles. ISSUE-121-FU-F adds the restock detail table; FU-G adds the net
    cash flow headline.
    """
    width = _receipt_cols(profile)
    ccode = (getattr(profile, 'currency', None) or 'PHP')

    def rrow(label, val):
        return _thermal_kv(label, val, width)

    def money(val):
        # payload money fields are plain strings like "100.00" / "-44.00".
        return format_currency(Decimal(str(val or 0)), ccode, ascii_only=True)

    def rule(ch='-'):
        return _thermal_rule(width, ch)

    def title(t):
        return _thermal_center(t, width)

    lines = []
    # --- Header block (mirrors the X/Z centered header) ---
    name = (getattr(profile, 'business_name', None) or 'WEEKLY REPORT')
    lines.append(name)  # printed double-height + centered by print_weekly_report
    addr = getattr(profile, 'business_address', None)
    if addr:
        for ln in str(addr).strip().splitlines():
            if ln.strip():
                lines.append(title(ln.strip()))

    lines.append(rule())
    lines.append(title('WEEKLY PERFORMANCE'))
    span = f"{payload['week_start']} - {payload['week_end']}"
    if payload.get('live_date'):
        span += ' (to date)'
    lines.append(title(span))

    s = payload['summary']
    lines.append(rule())
    lines.append(title('SUMMARY'))
    lines.append(rrow('Gross:', money(s['gross_total'])))
    lines.append(rrow('Net:', money(s['net_total'])))
    lines.append(rrow('Transactions:', s['transaction_count']))
    if s.get('avg_ticket') is not None:
        lines.append(rrow('Avg Ticket:', money(s['avg_ticket'])))
    lines.append(rrow('Voids:', s['void_count']))

    # ISSUE-121-FU-G: net cash flow headline (cash-basis: sales − restock spend,
    # NOT profit/COGS). Can be negative on a heavy-restock week — that's expected.
    lines.append(rule())
    lines.append(title('NET CASH FLOW'))
    lines.append(rrow('Sales - Restocks:', money(payload.get('net_cash_flow', '0'))))

    # Payments
    lines.append(rule())
    lines.append(title('PAYMENTS'))
    for label, key in (('Cash', 'cash_total'), ('GCash', 'gcash_total'),
                       ('Maya', 'maya_total'), ('Card', 'card_total')):
        lines.append(rrow(f'  {label}:', money(s[key])))

    # ISSUE-121-FU-B: restock cost (expense side) — per-ingredient summary.
    rc = payload.get('restock_costs') or {}
    lines.append(rule())
    lines.append(title('RESTOCK COST'))
    lines.append(rrow('Total:', money(rc.get('total', '0'))))
    for r in rc.get('by_ingredient', []):
        nm = r['name']
        val = money(r['cost'])
        max_name = width - len(val) - 3
        if max_name > 0 and len(nm) > max_name:
            nm = nm[:max_name]
        lines.append(rrow(f'  {nm}', val))

    # ISSUE-121-FU-F: per-restock detail table (date, ingredient, qty+unit,
    # cost, who restocked). Two compact lines per entry so it fits 58mm paper.
    detail = payload.get('restock_detail') or []
    if detail:
        lines.append(rule())
        lines.append(title('RESTOCK DETAIL'))
        for d in detail:
            date_s = str(d.get('date', ''))[5:]   # MM-DD
            cost = money(d.get('cost', '0'))
            head = f"{date_s} {d.get('ingredient', '')}"
            max_head = width - len(cost) - 1
            if max_head > 0 and len(head) > max_head:
                head = head[:max_head]
            lines.append(rrow(head, cost))
            qty = d.get('quantity', 0)
            unit = d.get('unit', '') or ''
            who = d.get('recorded_by', '—') or '—'
            lines.append(f"  {qty}{unit} by {who}"[:width])

    # Per-day breakdown (single column).
    lines.append(rule())
    lines.append(title('DAILY'))
    for d in payload.get('days', []):
        lines.append(rrow(f"{d['day_name']} {d['date'][5:]}", money(d['gross'])))

    # Top items.
    top = payload.get('top_items', [])
    if top:
        lines.append(rule())
        lines.append(title('TOP ITEMS'))
        for i, it in enumerate(top, 1):
            lines.append(rrow(f"{i}. {it['name']}"[:width - 6], f"x{it['quantity']}"))

    # Per-cashier.
    cashiers = payload.get('cashiers', [])
    if cashiers:
        lines.append(rule())
        lines.append(title('CASHIERS'))
        for c in cashiers:
            lines.append(rrow(f"{c['name']} ({c['txns']})", money(c['gross'])))

    lines.append(rule())
    return lines


def print_weekly_report(payload):
    """ISSUE-121-FU-D: emit the weekly report on the ESC/POS thermal printer.

    Reuses the same transport/width/encoding path as the X/Z reports. Returns
    True on success, False on any printer error (logged, never raised — mirrors
    the other print_* non-fatal contracts).
    """
    try:
        profile = BusinessProfile.objects.first()
        if not _is_printer_enabled(profile):
            logger.warning('Weekly report print skipped: printer not configured.')
            return False
        p = _get_transport(profile)
        try:
            lines = build_weekly_report_lines(payload, profile)
            # FU-H: business name in the same double-height centered style as
            # the X/Z headers, then the body left-aligned (titles pre-centered).
            _pset(p, profile, align='center', bold=True,
                  double_height=True, double_width=True)
            p.text((lines[0] if lines else 'WEEKLY REPORT') + '\n')
            _pset(p, profile, normal_textsize=True, align='left', bold=False)
            for ln in lines[1:]:
                p.text(ln + '\n')
            _pset(p, profile, align='center')
            p.cut()
        finally:
            p.close()
        return True
    except Exception as e:
        logger.warning(f'Weekly report print failed (non-fatal): {e}')
        return False


def kick_cash_drawer():
    """Send cashbox kick pulse via printer. Non-fatal.
    Pin configurable via settings.CASH_DRAWER_PIN: 0=pin2 (default), 1=pin5.
    """
    try:
        profile = BusinessProfile.objects.first()
        if not _is_printer_enabled(profile):
            return
        p = _get_transport(profile)
        p.set(font=_escpos_font(profile), align='left')
        try:
            drawer_pin = getattr(settings, 'CASH_DRAWER_PIN', 2)  # 2=pin2, 5=pin5 (escpos rejects 0)
            p.cashdraw(drawer_pin)
        finally:
            p.close()
    except Exception as e:
        logger.warning(f'Cash drawer kick failed (non-fatal): {e}')
