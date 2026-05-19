from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from escpos.printer import File
from .models import BusinessProfile
from .utils.currency import format_currency
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

RECEIPT_WIDTH = 32  # 58mm thermal paper @ ESC/POS Font B (384 dots / 12)
                    # Visual contract: docs/receipt-design.html (FEATURE-034)

def _truncate(text, max_len):
    """Truncate text with ellipsis if it exceeds max_len."""
    return text[:max_len - 3] + '...' if len(text) > max_len else text

def _get_printer():
    """Returns (transport, port, profile) if printing is enabled, else (None, None, None).
    Print transport is the local USB device /dev/usb/lp1; network printer_ip is optional."""
    profile = BusinessProfile.objects.first()
    if not profile or not profile.printer_enabled:
        return None, None, None
    return (profile.printer_ip or 'usb'), (profile.printer_port or 9100), profile

def print_receipt(transaction):
    """ESC/POS receipt print. Returns status dict. Never raises."""
    try:
        ip, port, profile = _get_printer()
        if not ip:
            return {'success': False, 'message': 'Printer not configured. Set up Business Profile in Settings.'}

        ccode = profile.currency if profile else 'PHP'
        p = File(_get_usb_device_path() or '/dev/usb/lp1')
        p.set(font='b', align='left')
        try:
            # Header — business name (large, bold, centered)
            p.set(align='center', bold=True, double_height=True, double_width=True)
            p.text((profile.business_name if profile else 'POS') + '\n')
            p.set(align='center', bold=False, double_height=False, double_width=False)

            # Tagline
            if profile and profile.tagline:
                p.text(profile.tagline + '\n')

            # Address (skip if blank; handle multi-line)
            if profile and profile.address:
                for line in profile.address.strip().splitlines():
                    line = line.strip()
                    if line:
                        p.text(line + '\n')

            # TIN (skip if blank)
            if profile and profile.tin:
                p.text(f'TIN: {profile.tin}\n')

            # MIN / Serial — BIR-mandated for accredited POS. Fields are
            # forward-compatible: render only when BusinessProfile gains
            # them via FLAG-056 (getattr with default keeps this safe today).
            min_no = getattr(profile, 'min_number', '') if profile else ''
            serial_no = getattr(profile, 'serial_number', '') if profile else ''
            if min_no:
                p.text(f'MIN: {min_no}\n')
            if serial_no:
                p.text(f'Serial: {serial_no}\n')

            p.text('-' * RECEIPT_WIDTH + '\n')

            # Receipt header text (skip if blank)
            if profile and profile.receipt_header:
                p.text(profile.receipt_header + '\n')

            # Transaction info
            p.set(align='left')
            p.text(f'Receipt #: {transaction.transaction_no}\n')
            p.text(f'Date: {transaction.created_at.strftime("%Y-%m-%d %H:%M")}\n')
            if transaction.cashier:
                p.text(f'Cashier: {transaction.cashier.username}\n')
            p.text('-' * RECEIPT_WIDTH + '\n')

            # Items — at 58mm/Font B the row is tight (32 chars). When the
            # name + qty_price would push past the width, the qty_price
            # wraps to an indented second line under the name (see
            # docs/receipt-design.html "Chicken Sandwich" sample).
            subtotal_sum = 0.0
            for item in transaction.items.select_related('item').all():
                qty_price = f'{item.quantity} x {float(item.unit_price):.2f}'
                subtotal = f'{float(item.subtotal):.2f}'
                subtotal_sum += float(item.subtotal)
                name = (item.item.name if item.item else 'Item')

                # Single-line layout: "name qty_price     subtotal"
                single_line_len = len(name) + 1 + len(qty_price) + 1 + len(subtotal)
                if single_line_len <= RECEIPT_WIDTH:
                    line = f'{name} {qty_price}'
                    padding = RECEIPT_WIDTH - len(line) - len(subtotal)
                    p.text(line + ' ' * max(padding, 1) + subtotal + '\n')
                else:
                    # Two-line layout: name + subtotal on row 1, qty_price indented on row 2
                    name_trunc = _truncate(name, RECEIPT_WIDTH - len(subtotal) - 1)
                    padding = RECEIPT_WIDTH - len(name_trunc) - len(subtotal)
                    p.text(name_trunc + ' ' * max(padding, 1) + subtotal + '\n')
                    p.text(f'  {qty_price}\n')

                for vs in item.variant_selections.all():
                    mod = float(vs.price_modifier or 0)
                    if mod > 0:
                        modifier_str = ' +' + format_currency(mod, ccode)
                    elif mod < 0:
                        modifier_str = ' ' + format_currency(mod, ccode)  # native '-' sign (ISSUE-074)
                    else:
                        modifier_str = ''
                    p.text(f'  {vs.group_name}: {vs.option_name}{modifier_str}\n')

            p.text('-' * RECEIPT_WIDTH + '\n')

            # Summary subtotal + total
            total = float(transaction.total_amount.amount) if hasattr(
                transaction.total_amount, 'amount') else float(transaction.total_amount)
            is_vat_exempt = getattr(transaction, 'vat_exempt', False)
            stored_vat_amount = float(getattr(transaction, 'vat_amount', 0) or 0)
            disc_type = getattr(transaction, 'discount_type', '')

            vat_inclusive = bool(profile and getattr(profile, 'vat_inclusive', False))
            subtotal_label = 'Subtotal (VAT-inc):' if vat_inclusive else 'Subtotal:'
            subtotal_val = format_currency(subtotal_sum, ccode)
            subtotal_padding = RECEIPT_WIDTH - len(subtotal_label) - len(subtotal_val)
            p.text(subtotal_label + ' ' * max(subtotal_padding, 1) + subtotal_val + '\n')

            # VAT removed line (only for VAT-exempt SC/PWD transactions)
            if is_vat_exempt and stored_vat_amount > 0:
                vat_pct = int(profile.vat_rate) if profile and hasattr(profile, 'vat_rate') else 12
                vat_rem_label = f'VAT ({vat_pct}%) Removed:'
                vat_rem_val = format_currency(-stored_vat_amount, ccode)
                vat_rem_padding = RECEIPT_WIDTH - len(vat_rem_label) - len(vat_rem_val)
                p.text(vat_rem_label + ' ' * max(vat_rem_padding, 1) + vat_rem_val + '\n')

            # Discount line (only when a discount was applied)
            discount = float(transaction.discount_amount) if transaction.discount_amount else 0.0
            if discount > 0:
                if disc_type == 'sc':
                    sc_rate = int(profile.sc_discount_rate) if profile and hasattr(profile, 'sc_discount_rate') else 20
                    disc_label = f'SC Discount ({sc_rate}%):'
                elif disc_type == 'pwd':
                    pwd_rate = int(profile.pwd_discount_rate) if profile and hasattr(profile, 'pwd_discount_rate') else 20
                    disc_label = f'PWD Discount ({pwd_rate}%):'
                elif disc_type == 'promo':
                    disc_label = 'Promo Discount:'
                else:
                    disc_label = 'Discount:'

                disc_val = format_currency(-discount, ccode)
                disc_padding = RECEIPT_WIDTH - len(disc_label) - len(disc_val)
                p.text(disc_label + ' ' * max(disc_padding, 1) + disc_val + '\n')

                # Print SC/PWD ID if provided
                id_number = getattr(transaction, 'discount_id_number', '')
                if id_number and disc_type in ('sc', 'pwd'):
                    id_prefix = 'SC ID: ' if disc_type == 'sc' else 'PWD ID: '
                    p.text(f'{id_prefix}{id_number}\n')

            p.set(bold=True)
            total_label = 'TOTAL:'
            total_val = format_currency(total, ccode)
            total_padding = RECEIPT_WIDTH - len(total_label) - len(total_val)
            p.text(total_label + ' ' * max(total_padding, 1) + total_val + '\n')
            p.set(bold=False)

            # VAT info line — only for non-exempt VAT-enabled transactions
            if profile and profile.vat_enabled and not is_vat_exempt:
                vat_rate = float(profile.vat_rate)
                vat_amount = total * vat_rate / (100 + vat_rate)
                vat_label = f'Incl. VAT ({vat_rate:.0f}%):'
                vat_val = format_currency(vat_amount, ccode)
                vat_padding = RECEIPT_WIDTH - len(vat_label) - len(vat_val)
                p.text(vat_label + ' ' * max(vat_padding, 1) + vat_val + '\n')

            # VAT-exempt declaration (RA 9994 for SC, RA 10754 for PWD)
            if is_vat_exempt:
                ra_ref = 'RA 9994' if disc_type == 'sc' else 'RA 10754'
                p.set(align='center', bold=True)
                p.text('VAT-EXEMPT TRANSACTION\n')
                p.set(bold=False)
                p.text(f'({ra_ref})\n')
                p.set(align='left')
            p.text(f'Payment: {transaction.get_payment_method_display()}\n')

            if transaction.payment_method == 'cash' and transaction.cash_received:
                cash_raw = transaction.cash_received.amount if hasattr(
                    transaction.cash_received, 'amount') else transaction.cash_received
                cash_dec = Decimal(str(cash_raw))
                total_dec = Decimal(str(total))
                change_dec = (cash_dec - total_dec).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                # 32-char layout — see docs/receipt-design.html (cash sample).
                p.text(f'Cash: {format_currency(cash_dec, ccode)}  Change: {format_currency(change_dec, ccode)}\n')
            elif transaction.payment_method in ('gcash', 'maya'):
                ref = transaction.gcash_reference or transaction.maya_reference or 'N/A'
                p.text(f'Ref#: {ref}\n')
                if transaction.customer_phone:
                    p.text(f'Phone: {transaction.customer_phone}\n')

            # Footer
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.set(align='center')
            if profile and profile.receipt_footer:
                p.text(profile.receipt_footer + '\n')
            else:
                p.text('Thank you!\n')

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
        ip, port, profile = _get_printer()
        if not ip:
            logger.warning('Z-report print skipped: printer not configured.')
            return False

        # Currency is the ONLY value still allowed from BP-derived state,
        # and even that is taken off the frozen ZReport snapshot.
        ccode = z_report.currency or 'PHP'
        p = File(_get_usb_device_path() or '/dev/usb/lp1')
        p.set(font='b', align='left')

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
        ip, port, profile = _get_printer()
        if not ip:
            return {'success': False, 'message': 'Printer not configured.'}
        ccode = profile.currency if profile else 'PHP'
        p = File(_get_usb_device_path() or '/dev/usb/lp1')
        p.set(font='b', align='left')
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
        ip, port, _profile = _get_printer()
        if not ip:
            return
        p = File(_get_usb_device_path() or '/dev/usb/lp1')
        p.set(font='b', align='left')
        try:
            drawer_pin = getattr(settings, 'CASH_DRAWER_PIN', 2)  # 2=pin2, 5=pin5 (escpos rejects 0)
            p.cashdraw(drawer_pin)
        finally:
            p.close()
    except Exception as e:
        logger.warning(f'Cash drawer kick failed (non-fatal): {e}')
