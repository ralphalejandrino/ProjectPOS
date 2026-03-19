from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction as db_transaction
from django.db.models import F
from django.core.exceptions import ValidationError
from djmoney.money import Money
from .models import Item, PosTransaction, PosTransactionItem, Shift
import threading
from .receipt_service import print_receipt, kick_cash_drawer

def create_pos_transaction(items_data, payment_method, cashier=None, **kwargs):
    """
    Service function to create a POS transaction, its items, and update inventory.
    Expects items_data as list of dicts: [{'item_id' or 'id': ..., 'quantity': ...}]
    """
    with db_transaction.atomic():
        from .models import BusinessProfile
        _bp = BusinessProfile.objects.first()
        _track_inventory = not _bp or _bp.track_inventory

        # Calculate total and validate stock up front
        total = Decimal('0.00')
        processed_items = []

        for item_entry in items_data:
            item_id = item_entry.get('item_id') or item_entry.get('id')
            quantity = item_entry.get('quantity', 0)

            # Select item for update to prevent race conditions
            item = Item.objects.get(id=item_id)

            if _track_inventory and item.stock < quantity:
                raise ValidationError(f"Insufficient stock for: {item.name}")

            subtotal = Decimal(str(item.price)) * Decimal(str(quantity))
            total += subtotal

            processed_items.append({
                'item': item,
                'quantity': quantity,
                'unit_price': item.price,
                'purchase_price': item.purchase_price,
                'subtotal': subtotal
            })

        # Apply discount
        discount_amount = kwargs.get('discount_amount', 0)
        discount_decimal = Decimal(str(discount_amount)) if discount_amount else Decimal('0.00')
        if discount_decimal > total:
            raise ValidationError(
                f"Discount (₱{discount_decimal}) cannot exceed order total (₱{total})"
            )
        final_total = total - discount_decimal

        # Attach the currently open shift, if any
        current_shift = Shift.objects.filter(is_open=True).order_by('-opened_at').first()

        # Create transaction
        cash_received = kwargs.get('cash_received')
        cash_received_amount = Decimal(str(cash_received)) if cash_received else None
        change_given = (cash_received_amount - final_total).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        ) if cash_received_amount else None

        transaction = PosTransaction.objects.create(
            total_amount=Money(final_total, 'PHP'),
            discount_amount=discount_decimal,
            discount_type=kwargs.get('discount_type', ''),
            discount_id_number=kwargs.get('discount_id_number', ''),
            status='completed',
            payment_method=payment_method,
            cash_received=cash_received_amount,
            change_given=change_given,
            gcash_reference=kwargs.get('gcash_reference', ''),
            maya_reference=kwargs.get('maya_reference', ''),
            card_reference=kwargs.get('card_reference', ''),
            cashier=cashier,
            shift=current_shift,
            customer_phone=kwargs.get('customer_phone', ''),
            transaction_no=kwargs.get('transaction_no')
        )

        # Create items and deduct stock
        for entry in processed_items:
            item = entry['item']
            PosTransactionItem.objects.create(
                pos_transaction=transaction,
                item=item,
                quantity=entry['quantity'],
                unit_price=entry['unit_price'],
                purchase_price=entry['purchase_price'],
                subtotal=entry['subtotal']
            )

            if _track_inventory:
                # Atomic check-and-decrement — single UPDATE WHERE, SQLite safe
                updated = Item.objects.filter(
                    pk=item.pk,
                    stock__gte=entry['quantity']
                ).update(stock=F('stock') - entry['quantity'])
                if updated == 0:
                    raise ValidationError(
                        f"Insufficient stock for: {item.name} (sold out during checkout)"
                    )

        # Fire-and-forget print + cashbox kick — never blocks the sale
        threading.Thread(target=print_receipt, args=(transaction,), daemon=True).start()
        threading.Thread(target=kick_cash_drawer, daemon=True).start()
        return transaction
