from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from escpos.printer import Network
from .models import BusinessProfile
import logging
logger = logging.getLogger(__name__)

RECEIPT_WIDTH = 42  # 80mm thermal paper standard

def _truncate(text, max_len):
    """Truncate text with ellipsis if it exceeds max_len."""
    return text[:max_len - 1] + '\u2026' if len(text) > max_len else text

def _get_printer():
    profile = BusinessProfile.objects.first()
    if not profile or not profile.printer_enabled or not profile.printer_ip:
        return None, None, None
    return profile.printer_ip, profile.printer_port or 9100, profile

def print_receipt(transaction):
    """ESC/POS receipt print. Returns status dict. Never raises."""
    try:
        ip, port, profile = _get_printer()
        if not ip:
            return {'success': False, 'message': 'Printer not configured. Set up Business Profile in Settings.'}

        p = Network(ip, port=port, timeout=3)

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

        # Items
        subtotal_sum = 0.0
        for item in transaction.items.select_related('item').all():
            qty_price = f'{item.quantity} x {float(item.unit_price):.2f}'
            subtotal = f'{float(item.subtotal):.2f}'
            subtotal_sum += float(item.subtotal)
            # Truncate name so name + qty_price + subtotal fits in RECEIPT_WIDTH
            max_name = RECEIPT_WIDTH - len(qty_price) - len(subtotal) - 2
            name = _truncate(item.item.name, max_name) if item.item else 'Item'
            line = f'{name} {qty_price}'
            padding = RECEIPT_WIDTH - len(line) - len(subtotal)
            p.text(line + ' ' * max(padding, 1) + subtotal + '\n')
            for vs in item.variant_selections.all():
                modifier_str = f'+PHP {float(vs.price_modifier):.2f}' if vs.price_modifier > 0 else ''
                p.text(f'  {vs.group_name}: {vs.option_name} {modifier_str}\n')

        p.text('-' * RECEIPT_WIDTH + '\n')

        # Summary subtotal + total
        total = float(transaction.total_amount.amount) if hasattr(
            transaction.total_amount, 'amount') else float(transaction.total_amount)
        is_vat_exempt = getattr(transaction, 'vat_exempt', False)
        stored_vat_amount = float(getattr(transaction, 'vat_amount', 0) or 0)
        disc_type = getattr(transaction, 'discount_type', '')

        subtotal_label = 'Subtotal (VAT-inc):' if (profile and profile.vat_enabled) or is_vat_exempt else 'Subtotal:'
        subtotal_val = f'PHP {subtotal_sum:.2f}'
        subtotal_padding = RECEIPT_WIDTH - len(subtotal_label) - len(subtotal_val)
        p.text(subtotal_label + ' ' * max(subtotal_padding, 1) + subtotal_val + '\n')

        # VAT removed line (only for VAT-exempt SC/PWD transactions)
        if is_vat_exempt and stored_vat_amount > 0:
            vat_rem_label = 'VAT (12%) Removed:'
            vat_rem_val = f'-PHP {stored_vat_amount:.2f}'
            vat_rem_padding = RECEIPT_WIDTH - len(vat_rem_label) - len(vat_rem_val)
            p.text(vat_rem_label + ' ' * max(vat_rem_padding, 1) + vat_rem_val + '\n')

        # Discount line (only when a discount was applied)
        discount = float(transaction.discount_amount) if transaction.discount_amount else 0.0
        if discount > 0:
            if disc_type == 'sc':
                disc_label = 'SC Discount (20%):'
            elif disc_type == 'pwd':
                disc_label = 'PWD Discount (20%):'
            elif disc_type == 'promo':
                disc_label = 'Promo Discount:'
            else:
                disc_label = 'Discount:'

            disc_val = f'-PHP {discount:.2f}'
            disc_padding = RECEIPT_WIDTH - len(disc_label) - len(disc_val)
            p.text(disc_label + ' ' * max(disc_padding, 1) + disc_val + '\n')

            # Print SC/PWD ID if provided
            id_number = getattr(transaction, 'discount_id_number', '')
            if id_number and disc_type in ('sc', 'pwd'):
                id_prefix = 'SC ID: ' if disc_type == 'sc' else 'PWD ID: '
                p.text(f'{id_prefix}{id_number}\n')

        p.set(bold=True)
        total_label = 'TOTAL:'
        total_val = f'PHP {total:.2f}'
        total_padding = RECEIPT_WIDTH - len(total_label) - len(total_val)
        p.text(total_label + ' ' * max(total_padding, 1) + total_val + '\n')
        p.set(bold=False)

        # VAT info line — only for non-exempt VAT-enabled transactions
        if profile and profile.vat_enabled and not is_vat_exempt:
            vat_rate = float(profile.vat_rate)
            vat_amount = total * vat_rate / (100 + vat_rate)
            vat_label = f'Incl. VAT ({vat_rate:.0f}%):'
            vat_val = f'PHP {vat_amount:.2f}'
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
            p.text(f'Cash: PHP {cash_dec:.2f}  Change: PHP {change_dec:.2f}\n')
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
        p.close()
        return {'success': True, 'message': 'Receipt printed.'}

    except Exception as e:
        logger.warning(f'Receipt print failed (non-fatal): {e}')
        return {'success': False, 'message': 'Printer error. Check connection.'}


def print_zreport_summary(data):
    """Print Z-report summary via ESC/POS. Returns status dict. Never raises."""
    try:
        ip, port, profile = _get_printer()
        if not ip:
            return {'success': False, 'message': 'Printer not configured.'}
        p = Network(ip, port=port, timeout=3)

        p.set(align='center', bold=True, double_height=True, double_width=True)
        p.text((profile.business_name if profile else 'Z-REPORT') + '\n')
        p.set(align='center', bold=False, double_height=False, double_width=False)
        if profile and profile.tagline:
            p.text(profile.tagline + '\n')
        if profile and profile.receipt_header:
            p.text(profile.receipt_header + '\n')
        p.text('-' * RECEIPT_WIDTH + '\n')
        p.set(align='center', bold=True)
        p.text('Z-REPORT — END OF DAY\n')
        p.set(align='left', bold=False)
        p.text(f"Date: {data.get('date', '')}\n")
        if data.get('generated_by'):
            p.text(f"By: {data['generated_by']}\n")
        p.text('-' * RECEIPT_WIDTH + '\n')

        def rrow(label, val):
            pad = RECEIPT_WIDTH - len(label) - len(val)
            return label + ' ' * max(pad, 1) + val + '\n'

        p.text(rrow('Gross Sales:', f"PHP {float(data.get('gross_sales', 0)):.2f}"))
        p.text(rrow('Voids:', f"PHP {float(data.get('void_total', 0)):.2f}"))
        p.set(bold=True)
        p.text(rrow('Net Sales:', f"PHP {float(data.get('net_sales', 0)):.2f}"))
        p.set(bold=False)
        p.text(rrow('Transactions:', str(data.get('transaction_count', 0))))
        p.text('-' * RECEIPT_WIDTH + '\n')
        for row in data.get('by_method', []):
            method = {'cash': 'Cash', 'gcash': 'GCash', 'maya': 'Maya'}.get(
                row.get('payment_method', ''), row.get('payment_method', 'Other'))
            p.text(rrow(f"  {method} ({row.get('count', 0)}):",
                        f"PHP {float(row.get('subtotal', 0)):.2f}"))
        discount_breakdown = data.get('discount_breakdown', [])
        if discount_breakdown:
            p.text('-' * RECEIPT_WIDTH + '\n')
            p.text('DISCOUNTS GIVEN:\n')
            for d in discount_breakdown:
                label = d.get('label', d.get('type', 'Discount'))[:16]
                amount = float(d.get('total_discount', 0))
                p.text(f"{label:<16}-PHP {amount:>10.2f}\n")
            total_disc = float(data.get('total_discounts_given', 0))
            p.text(f"{'Total':<16}-PHP {total_disc:>10.2f}\n")
        p.text('-' * RECEIPT_WIDTH + '\n')
        cash_expected = float(data.get('cash_expected', 0))
        p.text(rrow('Cash Expected:', f"PHP {cash_expected:.2f}"))
        if data.get('closing_cash') is not None:
            closing_cash = float(data['closing_cash'])
            over_short = closing_cash - cash_expected
            sign = '+' if over_short >= 0 else ''
            p.text(rrow('Closing Cash:', f"PHP {closing_cash:.2f}"))
            p.text(rrow('Over/Short:', f"PHP {sign}{over_short:.2f}"))
        else:
            p.text('Over/Short: ________________\n')
        p.text('-' * RECEIPT_WIDTH + '\n')
        p.set(align='center')
        if profile and profile.receipt_footer:
            p.text(profile.receipt_footer + '\n')
        p.cut()
        p.close()
        return {'success': True, 'message': 'Z-report printed.'}
    except Exception as e:
        logger.warning(f'Z-report print failed (non-fatal): {e}')
        return {'success': False, 'message': 'Printer error. Check connection.'}


def print_xreport_summary(data):
    """Print X-report summary via ESC/POS. Returns status dict. Never raises."""
    try:
        ip, port, profile = _get_printer()
        if not ip:
            return {'success': False, 'message': 'Printer not configured.'}
        p = Network(ip, port=port, timeout=3)

        p.set(align='center', bold=True, double_height=True, double_width=True)
        p.text((profile.business_name if profile else 'X-REPORT') + '\n')
        p.set(align='center', bold=False, double_height=False, double_width=False)
        if profile and profile.tagline:
            p.text(profile.tagline + '\n')
        if profile and profile.receipt_header:
            p.text(profile.receipt_header + '\n')
        p.text('-' * RECEIPT_WIDTH + '\n')
        p.set(align='center', bold=True)
        p.text('X-REPORT — SHIFT SUMMARY\n')
        p.set(align='left', bold=False)
        if data.get('cashier'):
            p.text(f"Cashier: {data['cashier']}\n")
        if data.get('opened_at'):
            p.text(f"Opened: {str(data['opened_at'])[:16]}\n")
        p.text('-' * RECEIPT_WIDTH + '\n')

        def rrow(label, val):
            pad = RECEIPT_WIDTH - len(label) - len(val)
            return label + ' ' * max(pad, 1) + val + '\n'

        p.text(rrow('Gross Sales:', f"PHP {float(data.get('gross_sales', 0)):.2f}"))
        p.text(rrow('Voids:', f"PHP {float(data.get('void_total', 0)):.2f}"))
        p.set(bold=True)
        p.text(rrow('Net Sales:', f"PHP {float(data.get('net_sales', 0)):.2f}"))
        p.set(bold=False)
        p.text(rrow('Transactions:', str(data.get('transaction_count', 0))))
        p.text('-' * RECEIPT_WIDTH + '\n')
        for row in data.get('by_payment_method', []):
            method = {'cash': 'Cash', 'gcash': 'GCash', 'maya': 'Maya'}.get(
                row.get('payment_method', ''), row.get('payment_method', 'Other'))
            p.text(rrow(f"  {method} ({row.get('count', 0)}):",
                        f"PHP {float(row.get('subtotal', 0)):.2f}"))
        p.text('-' * RECEIPT_WIDTH + '\n')
        p.set(align='center')
        if profile and profile.receipt_footer:
            p.text(profile.receipt_footer + '\n')
        p.cut()
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
        p = Network(ip, port=port, timeout=3)
        drawer_pin = getattr(settings, 'CASH_DRAWER_PIN', 0)  # 0=pin2, 1=pin5
        p.cashdraw(drawer_pin)
        p.close()
    except Exception as e:
        logger.warning(f'Cash drawer kick failed (non-fatal): {e}')
