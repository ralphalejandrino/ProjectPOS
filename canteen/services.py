from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction as db_transaction
from django.db.models import F
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from djmoney.money import Money
from .models import (
    Item, PosTransaction, PosTransactionItem, Shift,
    VariantGroup, VariantOption,
    CategoryVariantGroup, ProductVariantGroup, TransactionItemVariant,
    RecipeIngredient, Ingredient,
)
import threading
from .receipt_service import print_receipt, kick_cash_drawer


def _deplete_ingredients(item, variant_option_ids, quantity):
    """
    Deplete ingredient stock for a sold item.
    Variant recipes take priority over item recipes.
    Uses F() for atomic DB updates.
    Silently skips if no recipe is configured.
    """
    depleted_ingredient_ids = set()

    # Variant-level recipes first
    if variant_option_ids:
        variant_recipes = RecipeIngredient.objects.filter(
            variant_id__in=variant_option_ids
        ).select_related('ingredient')
        for recipe in variant_recipes:
            Ingredient.objects.filter(pk=recipe.ingredient.pk).update(
                current_stock=F('current_stock') - (recipe.quantity_used * quantity)
            )
            depleted_ingredient_ids.add(recipe.ingredient.pk)

    # Item-level recipes for ingredients not covered by variants
    item_recipes = RecipeIngredient.objects.filter(
        item=item
    ).select_related('ingredient')
    for recipe in item_recipes:
        if recipe.ingredient.pk not in depleted_ingredient_ids:
            Ingredient.objects.filter(pk=recipe.ingredient.pk).update(
                current_stock=F('current_stock') - (recipe.quantity_used * quantity)
            )


def _restore_ingredients(item, transaction_item, quantity):
    """
    Restore ingredient stock when a transaction is voided.
    Matches variant selections by option_name snapshot.
    Silently skips if no recipe is configured.
    """
    restored_ingredient_ids = set()

    # Get variant option IDs from snapshot names
    variant_option_ids = list(
        VariantOption.objects.filter(
            name__in=transaction_item.variant_selections.values_list('option_name', flat=True)
        ).values_list('id', flat=True)
    )

    # Variant-level restore first
    if variant_option_ids:
        variant_recipes = RecipeIngredient.objects.filter(
            variant_id__in=variant_option_ids
        ).select_related('ingredient')
        for recipe in variant_recipes:
            Ingredient.objects.filter(pk=recipe.ingredient.pk).update(
                current_stock=F('current_stock') + (recipe.quantity_used * quantity)
            )
            restored_ingredient_ids.add(recipe.ingredient.pk)

    # Item-level restore
    item_recipes = RecipeIngredient.objects.filter(
        item=item
    ).select_related('ingredient')
    for recipe in item_recipes:
        if recipe.ingredient.pk not in restored_ingredient_ids:
            Ingredient.objects.filter(pk=recipe.ingredient.pk).update(
                current_stock=F('current_stock') + (recipe.quantity_used * quantity)
            )


def create_pos_transaction(items_data, payment_method, cashier=None, **kwargs):
    """
    Service function to create a POS transaction, its items, and update inventory.
    Expects items_data as list of dicts: [{'item_id' or 'id': ..., 'quantity': ...}]
    """
    with db_transaction.atomic():
        if not items_data:
            raise DRFValidationError("Transaction must contain at least one item.")
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
            item = Item.objects.select_for_update().get(id=item_id)

            if _track_inventory and item.stock < quantity:
                raise ValidationError(f"Insufficient stock for: {item.name}")

            # Resolve variant selections for this item
            variant_selections = item_entry.get('variant_selections', [])  # list of {group_id, option_id}
            base_price = Decimal(str(item.price))
            modifier_total = Decimal('0.00')
            resolved_variants = []

            if variant_selections:
                # Get effective variant groups for this item
                # (category groups + product overrides)
                # Build category-level required overrides: {group_id: is_required_override or None}
                cat_required_override = {}
                cat_group_ids = set()
                if item.category_id:
                    for cvg in CategoryVariantGroup.objects.filter(category_id=item.category_id):
                        cat_group_ids.add(cvg.group_id)
                        cat_required_override[cvg.group_id] = cvg.is_required_override

                # Build product-level overrides: {group_id: (enabled, is_required_override)}
                prod_overrides = {
                    pvg.group_id: (pvg.enabled, pvg.is_required_override)
                    for pvg in ProductVariantGroup.objects.filter(product=item)
                }

                effective_group_ids = set()
                for gid in cat_group_ids:
                    enabled, _req = prod_overrides.get(gid, (True, None))
                    if enabled:
                        effective_group_ids.add(gid)
                for gid, (enabled, _req) in prod_overrides.items():
                    if enabled:
                        effective_group_ids.add(gid)

                effective_groups = {
                    g.id: g
                    for g in VariantGroup.objects.filter(id__in=effective_group_ids, is_active=True).prefetch_related('options')
                }

                # Build required_map using the same precedence as get_effective_variant_groups:
                # global default → category override → product override
                required_map = {}
                for gid, group in effective_groups.items():
                    required = group.is_required
                    if gid in cat_required_override and cat_required_override[gid] is not None:
                        required = cat_required_override[gid]
                    if gid in prod_overrides:
                        _enabled, prod_req = prod_overrides[gid]
                        if prod_req is not None:
                            required = prod_req
                    required_map[gid] = required

                # Validate required groups have a selection
                selected_group_ids = {sel.get('group_id') for sel in variant_selections}
                for gid, group in effective_groups.items():
                    if required_map[gid] and str(gid) not in {str(s) for s in selected_group_ids}:
                        raise DRFValidationError(f"'{group.name}' selection is required for {item.name}.")

                for sel in variant_selections:
                    group_id = sel.get('group_id')
                    option_id = sel.get('option_id')
                    try:
                        import uuid as _uuid
                        group = effective_groups[_uuid.UUID(str(group_id))]
                    except (KeyError, ValueError):
                        raise DRFValidationError(f"Variant group '{group_id}' is not valid for {item.name}.")

                    # Validate single groups have exactly one selection
                    if group.selection_type == 'single':
                        existing = [r for r in resolved_variants if r['group_id'] == group.id]
                        if existing:
                            raise DRFValidationError(f"'{group.name}' allows only one selection for {item.name}.")

                    option = group.options.filter(id=option_id, is_active=True).first()
                    if not option:
                        raise DRFValidationError(f"Option not found or inactive in group '{group.name}'.")

                    modifier_total += Decimal(str(option.price_modifier))
                    resolved_variants.append({
                        'group': group,
                        'option': option,
                        'group_id': group.id,
                        'option_id': option.id,   # required by _deplete_ingredients
                    })

            final_unit_price = base_price + modifier_total
            subtotal = final_unit_price * Decimal(str(quantity))
            total += subtotal

            processed_items.append({
                'item': item,
                'quantity': quantity,
                'unit_price': final_unit_price,   # backward compat: unit_price = final_price
                'base_price': base_price,
                'final_price': final_unit_price,
                'purchase_price': item.purchase_price,
                'subtotal': subtotal,
                'resolved_variants': resolved_variants,
            })

        # Apply discount — server-side validation
        discount_amount = kwargs.get('discount_amount', 0)
        discount_decimal = Decimal(str(discount_amount)) if discount_amount else Decimal('0.00')
        discount_type = kwargs.get('discount_type', '')
        _sc_pwd_vat_amount = Decimal('0.00')
        _is_vat_exempt = False

        if discount_decimal > total:
            raise DRFValidationError(
                f"Discount (₱{discount_decimal}) cannot exceed order total (₱{total})"
            )

        # Re-derive expected discount from BusinessProfile rates
        if discount_decimal > Decimal('0.00') and discount_type:
            _bp_check = _bp
            if discount_type == 'sc':
                rate = Decimal(str(_bp_check.sc_discount_rate if _bp_check else 20)) / Decimal('100')
                if _bp_check and _bp_check.vat_enabled:
                    vat_rate = Decimal(str(_bp_check.vat_rate)) / Decimal('100')
                    vat_exclusive = (total / (1 + vat_rate)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    _sc_pwd_vat_amount = (total - vat_exclusive).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    _is_vat_exempt = True
                else:
                    vat_exclusive = total
                expected = (vat_exclusive * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            elif discount_type == 'pwd':
                rate = Decimal(str(_bp_check.pwd_discount_rate if _bp_check else 20)) / Decimal('100')
                if _bp_check and _bp_check.vat_enabled:
                    vat_rate = Decimal(str(_bp_check.vat_rate)) / Decimal('100')
                    vat_exclusive = (total / (1 + vat_rate)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    _sc_pwd_vat_amount = (total - vat_exclusive).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    _is_vat_exempt = True
                else:
                    vat_exclusive = total
                expected = (vat_exclusive * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            elif discount_type == 'promo':
                if not (_bp_check and _bp_check.promo_discount_enabled):
                    raise DRFValidationError("Promo discounts are not enabled.")
                expected = (total * Decimal('0.50')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            else:
                expected = Decimal('0.00')
            if discount_decimal - expected > Decimal('0.01'):
                raise DRFValidationError(
                    f"Discount amount (₱{discount_decimal}) exceeds the allowed maximum "
                    f"(₱{expected}) for discount type '{discount_type}'."
                )

        # Require a non-empty ID number for SC/PWD discounts
        if discount_type in ('sc', 'pwd'):
            discount_id_number = kwargs.get('discount_id_number', '')
            if not discount_id_number or not str(discount_id_number).strip():
                raise DRFValidationError("SC/PWD ID number is required.")

        if _is_vat_exempt:
            final_total = (total - _sc_pwd_vat_amount - discount_decimal).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        else:
            final_total = total - discount_decimal

        # Attach the currently open shift, if any
        current_shift = Shift.objects.filter(
            cashier=cashier, is_open=True
        ).order_by('-opened_at').first() if cashier else None

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
            vat_exempt=_is_vat_exempt,
            vat_amount=_sc_pwd_vat_amount,
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
            txn_item = PosTransactionItem.objects.create(
                pos_transaction=transaction,
                item=item,
                quantity=entry['quantity'],
                unit_price=entry['unit_price'],
                base_price=entry['base_price'],
                final_price=entry['final_price'],
                purchase_price=entry['purchase_price'],
                subtotal=entry['subtotal'],
            )

            # Record variant selections as snapshots
            for rv in entry.get('resolved_variants', []):
                TransactionItemVariant.objects.create(
                    transaction_item=txn_item,
                    group_name=rv['group'].name,
                    option_name=rv['option'].name,
                    price_modifier=rv['option'].price_modifier,
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

                # Ingredient depletion
                variant_option_ids = [
                    rv.get('option_id') for rv in entry.get('resolved_variants', [])
                    if rv.get('option_id')
                ]
                _deplete_ingredients(item, variant_option_ids, entry['quantity'])

        # Fire-and-forget print + cashbox kick — never blocks the sale
        threading.Thread(target=print_receipt, args=(transaction,), daemon=True).start()
        threading.Thread(target=kick_cash_drawer, daemon=True).start()
        return transaction
