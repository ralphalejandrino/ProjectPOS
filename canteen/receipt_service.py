from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from escpos.printer import File, Network
from .models import BusinessProfile
from .utils.currency import format_currency
from .receipt_layout import build_receipt_rows, receipt_cols
import logging

# ISSUE-095: USB printer device auto-detect
_USB_DEVICE_CANDIDATES = ['/dev/usb/lp1', '/dev/usb/lp0', '/dev/lp0', '/dev/lp1']
_cached_usb_path = None

def _get_usb_device_path():
    """First openable USB printer device path, cached; None if none work."""
    global _cached_usb_path
    if _cached_usb_path is not None:
        return _cached_usb_path
    import os
    for p in _USB_DEVICE_CANDIDATES:
        if os.path.exists(p) and os.access(p, os.W_OK):
            logging.getLogger(__name__).info("Receipt printer USB device detected at: %s", p)
            _cached_usb_path = p
            return p
    logging.getLogger(__name__).warning("No USB printer device found. Tried: %s", _USB_DEVICE_CANDIDATES)
    return None
logger = logging.getLogger(__name__)

# Column count + the corrected width/font table now live in receipt_layout.py
# (single source of truth shared with the on-screen preview — FEATURE-040).

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
        target_dots = 576 if profile.paper_width == '80mm' else 384
        img = Image.open(path).convert('L')
        if img.width > target_dots:
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
            rows, cols, ccode = build_receipt_rows(transaction, profile)
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
        p.set(font=_escpos_font(profile), align='left')

        def rrow(label, val):
            pad = RECEIPT_WIDTH - len(label) - len(val)
            return label + ' ' * max(pad, 1) + val + '\n'

        def money(val):
            return format_currency(val, ccode)

        official = z_report.is_official

        try:
            # --- ISSUE-105: UNOFFICIAL top banner ---
            if not official:
                p.set(align='center', bold=True)
                p.text('=' * RECEIPT_WIDTH + '\n')
                p.set(align='center', bold=True,
                      double_height=True, double_width=False)
                p.text('*** UNOFFICIAL ***\n')
                p.text('NOT FOR BIR SUBMISSION\n')
                p.set(align='center', bold=True,
                      double_height=False, double_width=False)
                p.text('=' * RECEIPT_WIDTH + '\n')
                p.set(align='left', bold=False)

            # --- Header (frozen identity) ---
            p.set(align='center', bold=True, double_height=True, double_width=True)
            p.text((z_report.business_name or 'Z-REPORT') + '\n')
            p.set(align='center', bold=False, double_height=False, double_width=False)
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
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.set(align='center', bold=True)
            p.text('Z REPORT\n')
            p.set(align='left', bold=False)
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.text(rrow(f'Z #: {z_report.z_counter}',
                        f'Reset: {z_report.reset_counter}'))
            p.text(f'Business Date: {z_report.business_date}\n')
            started = z_report.started_at.strftime('%Y-%m-%d %H:%M')
            finalized = z_report.finalized_at.strftime('%Y-%m-%d %H:%M')
            p.text(f'Period: {started} - {finalized}\n')
            p.text(f'Cashier: {z_report.cashier.username}\n')
            p.text(rrow(f'From: {z_report.first_or_number or "-"}',
                        f'To: {z_report.last_or_number or "-"}'))
            p.text(f'Voided: {z_report.voided_count}\n')
            for orn in (z_report.voided_or_numbers or []):
                p.text(f'  VOID {orn}\n')

            # --- Sales summary ---
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.text(rrow('Gross Sales:', money(z_report.gross_sales)))
            p.text(rrow('Less Discounts:', money(-z_report.discount_total)))
            if z_report.sc_discount_total:
                p.text(rrow('  SC:', money(-z_report.sc_discount_total)))
            if z_report.pwd_discount_total:
                p.text(rrow('  PWD:', money(-z_report.pwd_discount_total)))
            if z_report.promo_discount_total:
                p.text(rrow('  Promo:', money(-z_report.promo_discount_total)))
            p.set(bold=True)
            p.text(rrow('Net Sales:', money(z_report.net_sales)))
            p.set(bold=False)
            p.text(rrow('Vatable Sales:', money(z_report.vatable_sales)))
            p.text(rrow('Output VAT:', money(z_report.output_vat)))
            p.text(rrow('VAT-Exempt Sales:', money(z_report.vat_exempt_sales)))
            p.text(rrow('Zero-Rated Sales:', money(z_report.zero_rated_sales)))

            # --- Payments ---
            p.text('-' * RECEIPT_WIDTH + '\n')
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
            p.set(bold=True)
            p.text(rrow('Total Payments:', money(total_payments)))
            p.set(bold=False)

            # --- Cash reconciliation ---
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.text(rrow('Opening Cash:', money(z_report.opening_cash)))
            p.text(rrow('Cash Collected:', money(z_report.cash_collected)))
            p.text(rrow('Cash Expected:', money(z_report.cash_expected)))
            if z_report.cash_counted is not None:
                p.text(rrow('Cash Counted:', money(z_report.cash_counted)))
            if z_report.over_short is not None:
                os_val = money(z_report.over_short)
                if z_report.over_short >= 0:
                    os_val = '+' + os_val
                p.text(rrow('Over/Short:', os_val))

            # --- Footer ---
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.set(bold=True)
            p.text(rrow('Grand Total Sales:',
                        money(z_report.grand_total_sales)))
            p.set(bold=False, align='center')
            p.text(f'Generated {finalized}\n')
            if not official:
                p.set(align='center', bold=True)
                p.text('=' * RECEIPT_WIDTH + '\n')
                p.text('*** UNOFFICIAL Z REPORT ***\n')
                p.text('=' * RECEIPT_WIDTH + '\n')
                p.set(align='center', bold=False)
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
        p.set(font=_escpos_font(profile), align='left')
        try:
            p.set(align='center', bold=True, double_height=True, double_width=True)
            p.text((profile.business_name if profile else 'X-REPORT') + '\n')
            p.set(align='center', bold=False, double_height=False, double_width=False)
            if profile and profile.tagline:
                p.text(profile.tagline + '\n')
            if profile and profile.receipt_header:
                p.text(profile.receipt_header + '\n')
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.set(align='center', bold=True)
            p.text('X-REPORT - SHIFT SUMMARY\n')
            p.set(align='left', bold=False)
            if data.get('cashier'):
                p.text(f"Cashier: {data['cashier']}\n")
            if data.get('opened_at'):
                p.text(f"Opened: {str(data['opened_at'])[:16]}\n")
            p.text('-' * RECEIPT_WIDTH + '\n')

            def rrow(label, val):
                pad = RECEIPT_WIDTH - len(label) - len(val)
                return label + ' ' * max(pad, 1) + val + '\n'

            p.text(rrow('Gross Sales:', format_currency(data.get('gross_sales', 0), ccode)))
            p.text(rrow('Voids:', format_currency(data.get('void_total', 0), ccode)))
            p.set(bold=True)
            p.text(rrow('Net Sales:', format_currency(data.get('net_sales', 0), ccode)))
            p.set(bold=False)
            p.text(rrow('Transactions:', str(data.get('transaction_count', 0))))
            p.text('-' * RECEIPT_WIDTH + '\n')
            for row in data.get('by_payment_method', []):
                method = {'cash': 'Cash', 'gcash': 'GCash', 'maya': 'Maya'}.get(
                    row.get('payment_method', ''), row.get('payment_method', 'Other'))
                p.text(rrow(f"  {method} ({row.get('count', 0)}):",
                            format_currency(row.get('subtotal', 0), ccode)))
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.set(align='center')
            if profile and profile.receipt_footer:
                p.text(profile.receipt_footer + '\n')
            p.cut()
        finally:
            p.close()
        return {'success': True, 'message': 'X-report printed.'}
    except Exception as e:
        logger.warning(f'X-report print failed (non-fatal): {e}')
        return {'success': False, 'message': 'Printer error. Check connection.'}


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
