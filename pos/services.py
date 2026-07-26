from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction as db_transaction
from django.db import IntegrityError
from django.db.models import F, Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError
from djmoney.money import Money
from .utils.currency import format_currency
from .models import (
    Item, PosTransaction, PosTransactionItem, Shift,
    VariantOption, TransactionItemVariant,
    RecipeIngredient, Ingredient, IngredientLog, IngredientRestockLog, PaymentLine,
)
import threading
from .receipt_service import kick_cash_drawer


def _move_ingredient(ingredient_pk, signed_delta, action, transaction, performed_by):
    """ISSUE-069: lock one ingredient row, snapshot before/after, apply the
    signed stock move, and append an IngredientLog row.

    ``signed_delta`` is negative for depletion (sale) and positive for restore
    (void). Returns silently without touching anything when the ingredient has
    FLAG-046 ``track_depletion=False``. Stock is allowed to go negative — that
    is the owner-investigate signal and must never block the sale/void.
    """
    ingredient = Ingredient.objects.select_for_update().get(pk=ingredient_pk)
    if not ingredient.track_depletion:
        return
    before = ingredient.current_stock
    after = before + signed_delta
    ingredient.current_stock = after
    ingredient.save(update_fields=['current_stock', 'updated_at'])
    IngredientLog.objects.create(
        ingredient=ingredient,
        action=action,
        quantity_change=signed_delta,
        stock_before=before,
        stock_after=after,
        transaction=transaction,
        performed_by=performed_by,
    )


# ---------------------------------------------------------------------------
# FEATURE-058: restock corrections (void / edit / re-attribute a restock entry)
# ---------------------------------------------------------------------------

_COST_Q = Decimal('0.0001')


def _weighted_avg_restock_cost(ingredient):
    """Correction-path cost basis: the replacement cost of what is currently ON
    THE SHELF. Walk non-voided purchases newest-to-oldest, taking only as much
    of each lot as is needed to cover ``current_stock`` (FIFO consumption: the
    oldest purchases are the ones already used up), and average what was taken.

    This keeps a correction from dragging cost toward long-consumed old prices:
    100kg@1.00 fully consumed + 100kg@2.00 on the shelf prices the shelf at
    2.00, not the all-time 1.50. A partially-needed oldest lot is weighted only
    by the portion still on the shelf. Deterministic and explainable ("the cost
    of the stock you still have"), unlike the FEATURE-051 rolling average whose
    exact history we can't replay.

    Edge cases: stock <= 0 → the newest purchase price (pure replacement cost);
    purchases don't cover the shelf (counts/adjustments added stock) → all
    purchases, i.e. the all-time average. Returns None when there is no
    non-voided purchase at all (caller restores the pre-purchase snapshot or
    leaves the cost untouched).
    """
    rows = list(
        ingredient.restock_logs.filter(is_voided=False)
        .order_by('-date', '-pk')
        .values_list('quantity_added', 'cost_per_unit'))
    if not rows:
        return None
    need = ingredient.current_stock
    if need <= 0:
        return rows[0][1].quantize(_COST_Q, rounding=ROUND_HALF_UP)
    total_q = Decimal('0')
    total_c = Decimal('0')
    for q, p in rows:
        take = q if q <= need else need
        total_q += take
        total_c += take * p
        need -= take
        if need <= 0:
            break
    return (total_c / total_q).quantize(_COST_Q, rounding=ROUND_HALF_UP)


def _apply_correction_stock(ingredient, signed_delta, user, note):
    """Apply a signed stock delta from a correction and append a 'correction'
    ledger row (before/after snapshot). Unlike sale depletion this ignores
    track_depletion — a purchase correction must move stock regardless — and
    lets stock go negative (owner-investigate signal, per ISSUE-069). Returns
    (before, after)."""
    if ingredient.is_preparation:
        # Tripwire, not UX: the verbs reject preps with a friendly error first.
        # This catches any FUTURE caller (v2, a backfill script) that forgets.
        raise ValueError('correction primitives must not touch a preparation')
    before = ingredient.current_stock
    after = before + signed_delta
    ingredient.current_stock = after
    ingredient.save(update_fields=['current_stock', 'updated_at'])
    IngredientLog.objects.create(
        ingredient=ingredient, action='correction',
        quantity_change=signed_delta, stock_before=before, stock_after=after,
        performed_by=user, notes=note[:255],
    )
    return before, after


def _recost(ingredient):
    """Reset the ingredient's cost to the non-voided restock weighted average, if
    derivable. No-op when there is no purchase history to derive from."""
    if ingredient.is_preparation:
        raise ValueError('correction primitives must not touch a preparation')
    c = _weighted_avg_restock_cost(ingredient)
    if c is not None:
        ingredient.cost_per_unit = c
        ingredient.save(update_fields=['cost_per_unit'])
    return c


def _reject_preparation_correction(ingredient):
    """FEATURE-058 v1 scope guard: corrections are for PURCHASED ingredients.
    A preparation's cost is blended from batch production (produce_batch),
    which writes no restock rows — so recomputing its cost from restocks alone
    (_recost) would silently erase the production-derived component. Blocked
    until v2 handles preparation/component corrections."""
    if getattr(ingredient, 'is_preparation', False):
        raise DRFValidationError(
            f'{ingredient.name} is a preparation — its cost comes from batch '
            'production, so restock corrections are not supported on it yet. '
            'Adjust it with a new batch or a stock adjustment instead.')


def _locked_fresh_restock(restock):
    """Re-fetch the restock row inside the open transaction. The instance the
    view loaded was read BEFORE any lock: two concurrent corrections would both
    see is_voided=False and double-apply the stock/cost effects (the second
    blocks on the ingredient row lock, then passes its stale in-memory check).
    Reading the current row after acquiring the ingredient lock closes that
    window — the loser now sees the winner's committed void/edit."""
    return (IngredientRestockLog.objects.select_related('ingredient')
            .select_for_update().get(pk=restock.pk))


def _mark_voided(restock, user, note):
    restock.is_voided = True
    restock.voided_at = timezone.now()
    restock.voided_by = user
    if note:
        restock.correction_note = note
    restock.save(update_fields=[
        'is_voided', 'voided_at', 'voided_by', 'correction_note'])


def _note(base, reason):
    """Ledger note with an optional reason — no dangling '()' when the manager
    left the reason blank (the UI's Void path always sends '')."""
    return f'{base} ({reason})' if reason else base


def _clear_stale_price_memory(ingredient, restock):
    """FEATURE-050 remembers the last package price on the ingredient and the
    next package restock silently reuses it when the price field is left blank.
    If the entry just voided/re-priced was the LATEST purchase, that memory may
    be exactly the fat-fingered price being corrected — clear it so the next
    restock asks for the price instead of resurrecting the mistake. (Cleared
    only when no newer non-voided restock exists; we cannot reconstruct the
    prior value because package price is not stored per-row.)"""
    if ingredient.last_purchase_price is None:
        return
    newer = ingredient.restock_logs.filter(
        is_voided=False, date__gt=restock.date).exclude(pk=restock.pk).exists()
    if not newer:
        ingredient.last_purchase_price = None
        ingredient.save(update_fields=['last_purchase_price'])


def void_restock(restock, *, user, reason=''):
    """Soft-void a restock entry: mark it voided (kept for audit), remove its
    stock contribution, and recompute the ingredient's cost from the remaining
    purchases. Idempotent-guarded (a voided entry cannot be voided again)."""
    _reject_preparation_correction(restock.ingredient)
    with db_transaction.atomic():
        ing = Ingredient.objects.select_for_update().get(pk=restock.ingredient_id)
        restock = _locked_fresh_restock(restock)
        if restock.is_voided:
            raise DRFValidationError('This restock entry has already been voided.')
        _mark_voided(restock, user, reason)
        _, after = _apply_correction_stock(
            ing, -restock.quantity_added, user,
            _note('void restock #%d' % restock.pk, reason))
        if _recost(ing) is None and restock.cost_before is not None:
            # No purchase survives to derive a cost from — restore the honest
            # pre-purchase snapshot instead of keeping the voided entry's roll.
            ing.cost_per_unit = restock.cost_before
            ing.save(update_fields=['cost_per_unit'])
        _clear_stale_price_memory(ing, restock)
        return {'restock': restock, 'ingredient': ing, 'new_stock': after,
                'negative_stock': after < 0}


def edit_restock(restock, *, quantity_added=None, cost_per_unit=None,
                 user, reason=''):
    """Edit a restock's quantity and/or price. Applies the stock difference and
    recomputes cost from the corrected purchase set. A voided entry cannot be
    edited (re-instate it by editing a fresh entry instead)."""
    _reject_preparation_correction(restock.ingredient)
    with db_transaction.atomic():
        ing = Ingredient.objects.select_for_update().get(pk=restock.ingredient_id)
        restock = _locked_fresh_restock(restock)
        if restock.is_voided:
            raise DRFValidationError('A voided restock entry cannot be edited.')
        # Values equal to what is stored are not edits. This also stops a
        # replayed/duplicate POST from re-running the recompute and falsely
        # stamping "corrected by" on an entry nothing changed on.
        if quantity_added is not None and quantity_added == restock.quantity_added:
            quantity_added = None
        if cost_per_unit is not None and cost_per_unit == restock.cost_per_unit:
            cost_per_unit = None
        if quantity_added is None and cost_per_unit is None:
            raise DRFValidationError('Nothing to edit — pass a quantity or a price.')
        old_q = restock.quantity_added
        if quantity_added is not None:
            restock.quantity_added = quantity_added
        if cost_per_unit is not None:
            restock.cost_per_unit = cost_per_unit
        restock.corrected_at = timezone.now()
        restock.corrected_by = user
        if reason:
            restock.correction_note = reason
        # update_fields path → does NOT re-trigger the FEATURE-051 insert roll.
        restock.save(update_fields=[
            'quantity_added', 'cost_per_unit', 'corrected_at', 'corrected_by',
            'correction_note'])
        delta = restock.quantity_added - old_q
        after = ing.current_stock
        if delta != 0:
            _, after = _apply_correction_stock(
                ing, delta, user,
                _note(f'edit restock #{restock.pk} qty {old_q}->{restock.quantity_added}',
                      reason))
        _recost(ing)
        if cost_per_unit is not None:
            _clear_stale_price_memory(ing, restock)
        return {'restock': restock, 'ingredient': ing, 'new_stock': after,
                'negative_stock': after < 0}


def reattribute_restock(restock, *, target_ingredient, quantity_added,
                        cost_per_unit, user, reason=''):
    """Move a restock to the correct ingredient. The original is soft-voided on
    the source (stock removed, cost recomputed); a NEW correctly-attributed
    restock is recorded on the target in the target's own units (so a ml→scoop
    mis-tap can't carry the wrong number over) and on the ORIGINAL purchase
    date (the goods arrived when they arrived — the weekly restock-spend report
    must not shift the purchase into the correction week). Returns the new
    restock."""
    _reject_preparation_correction(restock.ingredient)
    _reject_preparation_correction(target_ingredient)
    if target_ingredient.pk == restock.ingredient_id:
        raise DRFValidationError('Target is the same ingredient — use edit instead.')
    with db_transaction.atomic():
        src = Ingredient.objects.select_for_update().get(pk=restock.ingredient_id)
        restock = _locked_fresh_restock(restock)
        if restock.is_voided:
            raise DRFValidationError('A voided restock entry cannot be re-attributed.')
        note = reason or f're-attributed to {target_ingredient.name}'
        _mark_voided(restock, user, note)
        _apply_correction_stock(
            src, -restock.quantity_added, user,
            f'void restock #{restock.pk}: {note}')
        if _recost(src) is None and restock.cost_before is not None:
            src.cost_per_unit = restock.cost_before
            src.save(update_fields=['cost_per_unit'])
        _clear_stale_price_memory(src, restock)

        # Record the corrected purchase on the target, dated at the ORIGINAL
        # purchase. Its own save() runs the FEATURE-051 roll (a genuine new
        # purchase on the target).
        tgt = Ingredient.objects.select_for_update().get(pk=target_ingredient.pk)
        before = tgt.current_stock
        new = IngredientRestockLog(
            ingredient=tgt, quantity_added=quantity_added,
            cost_per_unit=cost_per_unit, recorded_by=user, date=restock.date,
            corrected_by=user, corrected_at=timezone.now(),
            notes=f're-attributed from restock #{restock.pk}')
        # FLAG-078: this movement is logged below as a 'correction' (a
        # re-attribution, not a fresh purchase), so suppress save()'s automatic
        # 'restock' ledger row to avoid double-logging the target.
        new.save(log_movement=False)
        tgt.refresh_from_db()
        IngredientLog.objects.create(
            ingredient=tgt, action='correction', quantity_change=quantity_added,
            stock_before=before, stock_after=tgt.current_stock,
            performed_by=user, notes=f're-attributed in from restock #{restock.pk}')
        return {'restock': restock, 'new_restock': new, 'source': src,
                'target': tgt, 'negative_stock': src.current_stock < 0}


# FEATURE-046: read-time "makeable" stock — how many units of a recipe item
# could be produced from current ingredient inventory. This is a pure read-side
# overlay alongside the stored Item.stock counter (the owner's hand-count that
# gates and is decremented by every sale at services.create_pos_transaction).
# It NEVER gates a sale and is never persisted — zero migration.
def compute_makeable(recipe_lines):
    """Return (makeable, status) for an iterable of direct item-level
    RecipeIngredient lines.

    makeable = min over lines of floor(ingredient.current_stock / quantity_used),
    clamped at 0 (an oversold ingredient with negative stock — ISSUE-069 — means
    "can make 0 now", never a negative count).

    status:
      'no_recipe'         no lines → makeable None; a pure stocked good, the
                          stored Item.stock counter is the only truth.
      'incomplete_recipe' any line has quantity_used None or <= 0 → makeable
                          None. A 0-quantity line must not raise ZeroDivisionError
                          and must not be silently skipped — it flags the whole
                          item incomplete (recipe data-entry error).
      'ok'                every line valid → numeric makeable (scarcest binds).
    """
    lines = list(recipe_lines)
    if not lines:
        return None, 'no_recipe'
    makeable = None
    for line in lines:
        q = line.quantity_used
        if q is None or q <= 0:
            return None, 'incomplete_recipe'
        batches = int(line.ingredient.current_stock // q)  # Decimal floor
        makeable = batches if makeable is None else min(makeable, batches)
    return max(makeable, 0), 'ok'


def item_makeable(item):
    """compute_makeable over an Item's DIRECT recipe lines only (item-FK).

    Variant-only items (recipe attached via their variants, no direct lines)
    have no resolvable recipe at the no-variant-selected surface, and the stored
    Item.stock counter tracks them identically to a pure good — so they classify
    as 'no_recipe' here. We never resolve/guess a variant for this surface.

    Relies on the ``recipe_ingredients__ingredient`` prefetch (ItemViewSet) — no
    per-item query when prefetched.

    BUG-013: variant lines now also carry ``item`` (per-item variant recipes),
    so the base recipe is the item's lines with ``variant`` null. Filtered in
    Python to keep the prefetch intact (a queryset .filter() would re-query).
    """
    base_lines = [
        line for line in item.recipe_ingredients.all() if line.variant_id is None
    ]
    return compute_makeable(base_lines)


def item_recipe_cost(item):
    """FEATURE-052: per-unit COGS of a recipe item, derived from its ingredients.

    cost = sum(quantity_used * ingredient.cost_per_unit) over the item's DIRECT
    (variant-null) recipe lines — the single source of truth being the
    ingredient's weighted-average cost (FEATURE-051). Returns None for a
    non-recipe (pure resale) item so callers fall back to the manual
    purchase_price. Lines with a missing/non-positive quantity are skipped (they
    surface separately as makeable_status 'incomplete_recipe').

    Relies on the ``recipe_ingredients__ingredient`` prefetch (ItemViewSet) — no
    per-item query when prefetched. Filtered in Python to keep the prefetch
    intact.
    """
    base_lines = [
        line for line in item.recipe_ingredients.all() if line.variant_id is None
    ]
    if not base_lines:
        return None
    total = Decimal('0')
    for line in base_lines:
        q = line.quantity_used
        if q is None or q <= 0:
            continue
        total += q * line.ingredient.cost_per_unit
    return total.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def item_effective_unit_cost(item, variant_option_ids):
    """Per-unit COGS at sale time INCLUDING the selected variants' recipe
    lines — the cost twin of _deplete_ingredients, with identical semantics:
    a selected variant line costs its own quantity; a 'replace' line
    substitutes the base line for that ingredient while an 'add' line stacks
    on it; unselected variant lines cost nothing.

    item_recipe_cost only reads base (variant-null) lines, so an item whose
    recipe is encoded per size variant (the PROD menu shape — no base lines at
    all) snapshotted its purchase_price (0) on every sale and could never show
    a margin. Returns None only when the item has NO recipe lines at all, so
    the caller falls back to the resale purchase_price. Lines with a
    missing/non-positive quantity are skipped, as in item_recipe_cost."""
    lines = list(
        item.recipe_ingredients.select_related('ingredient').all()
    )
    if not lines:
        return None
    selected = set(variant_option_ids or [])
    replaced = set()
    chosen = []
    for line in lines:
        if line.variant_id is not None and line.variant_id in selected:
            chosen.append(line)
            if line.depletion_mode == 'replace':
                replaced.add(line.ingredient_id)
    for line in lines:
        if line.variant_id is None and line.ingredient_id not in replaced:
            chosen.append(line)
    total = Decimal('0')
    for line in chosen:
        q = line.quantity_used
        if q is None or q <= 0:
            continue
        total += q * line.ingredient.cost_per_unit
    return total.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def produce_batch(preparation, num_batches, performed_by=None, notes=''):
    """FEATURE-056: 'prep a batch' — make ``num_batches`` of a preparation.

    A preparation is an Ingredient (is_preparation) built from other ingredients
    (PreparationComponent). Producing:
      1. deducts each component: quantity_used * num_batches,
      2. adds batch_yield * num_batches to the preparation's stock,
      3. rolls the batch cost into the preparation's weighted-average
         cost_per_unit using the SAME formula as a restock
         (IngredientRestockLog.save) so downstream drink COGS stays a single
         source of truth.

    batch unit cost = Σ(component.quantity_used * component.cost_per_unit)
                      / preparation.batch_yield.

    Components are ordinary Ingredients, so a raw material shared between a
    direct drink recipe and a batch keeps ONE stock and ONE cost — no
    duplication, no double-count. The prep is depleted (once) when a drink using
    it is sold; components are depleted (once) here at production time.

    All writes happen under row locks (deterministic pk order) so a concurrent
    sale/restock cannot clobber the read-modify-write. Stock may go negative —
    the owner-investigate signal (ISSUE-069), never blocked.
    """
    from django.db import transaction as _tx
    from .models import Ingredient, PreparationComponent, IngredientLog

    num_batches = Decimal(str(num_batches))
    if num_batches <= 0:
        raise DRFValidationError("Number of batches must be greater than zero.")
    if not preparation.is_preparation:
        raise DRFValidationError(f"{preparation.name} is not a preparation.")
    if not preparation.batch_yield or preparation.batch_yield <= 0:
        raise DRFValidationError(
            "Set a batch yield greater than zero before producing a batch."
        )

    with _tx.atomic():
        prep = Ingredient.objects.select_for_update().get(pk=preparation.pk)
        components = list(
            PreparationComponent.objects.filter(preparation=prep)
            .select_related('component')
        )
        if not components:
            raise DRFValidationError(
                "Add at least one component before producing a batch."
            )

        # Lock component rows in a deterministic order (pk) to avoid deadlocks
        # with the sale-depletion path (which also locks ingredient rows).
        comp_ids = sorted(c.component_id for c in components)
        locked = {
            i.pk: i for i in
            Ingredient.objects.select_for_update().filter(pk__in=comp_ids)
        }

        # Batch cost from the CURRENT (locked) component weighted-avg costs.
        batch_cost = Decimal('0')
        for c in components:
            batch_cost += c.quantity_used * locked[c.component_id].cost_per_unit

        # 1. Deplete components (explicit production consumption — deducted even
        #    for 'counted' components since this is a deliberate make event).
        for c in components:
            comp = locked[c.component_id]
            delta = -(c.quantity_used * num_batches)
            before = comp.current_stock
            after = before + delta
            comp.current_stock = after
            comp.save(update_fields=['current_stock', 'updated_at'])
            IngredientLog.objects.create(
                ingredient=comp, action='production', quantity_change=delta,
                stock_before=before, stock_after=after,
                performed_by=performed_by,
                notes=(notes or f"Batch of {prep.name}")[:255],
            )

        # 2. + 3. Add yield and roll the weighted-average cost into the prep.
        produced = prep.batch_yield * num_batches
        unit_cost = (batch_cost / prep.batch_yield).quantize(
            Decimal('0.0001'), rounding=ROUND_HALF_UP
        )
        old_qty = prep.current_stock
        old_cost = prep.cost_per_unit
        new_qty = old_qty + produced
        if old_qty <= 0 or new_qty <= 0:
            new_cost = unit_cost
        else:
            new_cost = (
                (old_qty * old_cost + produced * unit_cost) / new_qty
            ).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        before = prep.current_stock
        prep.current_stock = new_qty
        prep.cost_per_unit = new_cost
        prep.save(update_fields=['current_stock', 'cost_per_unit', 'updated_at'])
        IngredientLog.objects.create(
            ingredient=prep, action='production', quantity_change=produced,
            stock_before=before, stock_after=new_qty, performed_by=performed_by,
            notes=(notes or f"Produced {num_batches} batch(es)")[:255],
        )

    return {
        'preparation': prep.name,
        'batches': str(num_batches),
        'produced_qty': str(produced),
        'batch_cost': str(batch_cost.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)),
        'unit_cost': str(unit_cost),
        'new_stock': str(new_qty),
        'new_cost_per_unit': str(new_cost),
    }


def _deplete_ingredients(item, variant_option_ids, quantity,
                         transaction=None, performed_by=None):
    """
    Deplete ingredient stock for a sold item and write the IngredientLog ledger.
    Variant recipes take priority over item recipes.

    ISSUE-069: each depletion locks the ingredient row (select_for_update),
    snapshots stock_before/stock_after, and writes an IngredientLog(action='sale')
    attributed to ``performed_by`` and linked to ``transaction``. Per-ingredient
    FLAG-046 gate (track_depletion) lives in _move_ingredient.
    Silently skips if no recipe is configured.
    """
    depleted_ingredient_ids = set()

    # Variant-level recipes first
    if variant_option_ids:
        # BUG-013: scope to THIS item's variant lines. The VariantOption is
        # shared across products, so filtering on variant alone depleted every
        # item's recipe for that option. Identity is (item, variant).
        variant_recipes = RecipeIngredient.objects.filter(
            item=item, variant_id__in=variant_option_ids
        ).select_related('ingredient')
        for recipe in variant_recipes:
            _move_ingredient(
                recipe.ingredient.pk, -(recipe.quantity_used * quantity),
                'sale', transaction, performed_by,
            )
            # BUG-003: only a 'replace' line suppresses the item-level line for
            # this ingredient (substitution). An 'add' line depletes its own
            # amount but leaves the base line to deplete and sum (additive
            # add-ons/sizes).
            if recipe.depletion_mode == 'replace':
                depleted_ingredient_ids.add(recipe.ingredient.pk)

    # Item-level (base) recipes for ingredients not covered by variants.
    # BUG-013: variant lines now also carry item, so the base recipe is the
    # subset with variant null — without this filter the variant lines above
    # would be depleted a second time here.
    item_recipes = RecipeIngredient.objects.filter(
        item=item, variant__isnull=True
    ).select_related('ingredient')
    for recipe in item_recipes:
        if recipe.ingredient.pk not in depleted_ingredient_ids:
            _move_ingredient(
                recipe.ingredient.pk, -(recipe.quantity_used * quantity),
                'sale', transaction, performed_by,
            )


def _restore_ingredients(item, transaction_item, quantity,
                         transaction=None, performed_by=None, action='void'):
    """
    Restore ingredient stock when a transaction is voided (action='void') or
    refunded (FEATURE-015, action='refund') and mirror the move into the
    IngredientLog ledger (ISSUE-069). ``action`` tags the ledger rows so void
    and refund restores stay distinguishable in the audit trail.
    Matches variant selections by (group_name, option_name) pair snapshot.

    ISSUE-072: matching by option_name alone is ambiguous — if two variant
    groups each define an option with the same name (e.g. both have "Large"),
    a name-only match restores both groups' ingredients. We match on the full
    (group_name, option_name) pair so only the correct group's recipe is
    restored.
    Silently skips if no recipe is configured.
    """
    restored_ingredient_ids = set()

    # Resolve variant option IDs from snapshot (group_name, option_name) pairs.
    name_pairs = list(transaction_item.variant_selections.values_list(
        'group_name', 'option_name'
    ))
    variant_option_ids = []
    if name_pairs:
        pair_filter = Q()
        for group_name, option_name in name_pairs:
            pair_filter |= Q(group__name=group_name, name=option_name)
        variant_option_ids = list(
            VariantOption.objects.filter(pair_filter).values_list('id', flat=True)
        )

    # Variant-level restore first
    if variant_option_ids:
        # BUG-013: mirror _deplete_ingredients — restore only THIS item's
        # variant lines, not every item that shares the option. Keeps void/
        # restore symmetric with the per-item depletion above.
        variant_recipes = RecipeIngredient.objects.filter(
            item=item, variant_id__in=variant_option_ids
        ).select_related('ingredient')
        for recipe in variant_recipes:
            _move_ingredient(
                recipe.ingredient.pk, (recipe.quantity_used * quantity),
                action, transaction, performed_by,
            )
            # BUG-003: mirror the sale path exactly — only a 'replace' line
            # suppressed the base on sale, so only it suppresses the base
            # restore here. An 'add' line restored its own amount alongside the
            # base, keeping void/restore symmetric with depletion.
            if recipe.depletion_mode == 'replace':
                restored_ingredient_ids.add(recipe.ingredient.pk)

    # Item-level (base) restore. BUG-013: exclude variant lines (they now carry
    # item) so the base restore mirrors the base depletion exactly.
    item_recipes = RecipeIngredient.objects.filter(
        item=item, variant__isnull=True
    ).select_related('ingredient')
    for recipe in item_recipes:
        if recipe.ingredient.pk not in restored_ingredient_ids:
            _move_ingredient(
                recipe.ingredient.pk, (recipe.quantity_used * quantity),
                action, transaction, performed_by,
            )


def stock_movements_for_shift(shift):
    """FEATURE-008: per-ingredient sold/voided quantities for a shift, read
    live from the IngredientLog ledger (no schema change, no new migration).

    Scoped via ``transaction__shift`` so only sale/void rows tied to this
    shift's transactions are counted. ``sold`` and ``voided`` are positive
    magnitudes (sale rows carry a negative quantity_change, void rows a
    positive one). Ingredients with FLAG-046 track_depletion=False never wrote
    sale rows, so they implicitly produce no entries. Returns [] when nothing
    moved. Uses select_related('ingredient') — one query, no N+1.
    """
    if shift is None:
        return []
    # FLAG-047: skip ledger rows tied to seed transactions — a quarantined
    # demo sale/void must not surface in the X/Z stock-movement section.
    logs = (
        IngredientLog.objects
        .filter(transaction__shift=shift, action__in=['sale', 'void'])
        .exclude(transaction__is_seed=True)
        .select_related('ingredient')
    )
    agg = {}
    for log in logs:
        entry = agg.get(log.ingredient_id)
        if entry is None:
            entry = {
                'ingredient_id': log.ingredient_id,
                'ingredient_name': log.ingredient.name,
                'sold': Decimal('0'),
                'voided': Decimal('0'),
            }
            agg[log.ingredient_id] = entry
        if log.action == 'sale':
            entry['sold'] += -log.quantity_change
        else:  # void
            entry['voided'] += log.quantity_change
    return [
        {
            'ingredient_id': e['ingredient_id'],
            'ingredient_name': e['ingredient_name'],
            'sold': float(e['sold']),
            'voided': float(e['voided']),
        }
        for e in sorted(agg.values(), key=lambda e: e['ingredient_name'].lower())
    ]


@db_transaction.atomic
def open_shift(cashier_user, opening_cash):
    """ISSUE-107: open a new shift for the cashier.

    Raises ValidationError if opening_cash < 0, or if the cashier already
    has an open shift (the one_open_shift_per_cashier partial unique
    constraint surfaces as IntegrityError and is translated to a friendly
    message). Decimal in, never float.
    """
    if not isinstance(opening_cash, Decimal):
        opening_cash = Decimal(str(opening_cash))
    if opening_cash < 0:
        raise ValidationError("Opening cash cannot be negative.")
    try:
        return Shift.objects.create(
            cashier=cashier_user,
            opening_cash=opening_cash,
            is_open=True,
        )
    except IntegrityError:
        raise ValidationError(
            "You already have an open shift. "
            "Close it before opening a new one."
        )


def resolve_effective_variant_groups(item):
    """FLAG-049: single source of truth for an item's effective variant groups.

    Consolidates the resolution that previously lived in two places — the
    inline block of ``create_pos_transaction`` (sale validation) and
    ``ItemSerializer.get_effective_variant_groups`` (product API). Both now
    call this.

    Resolution precedence:
      * category assignment (CategoryVariantGroup) provides the base set;
      * product overrides (ProductVariantGroup) enable/disable groups;
      * ``required`` resolves global default → category override → product
        override, each only when it is not None.
    Inactive groups are dropped.

    Returns an ordered list (VariantGroup Meta ordering: sort_order, name) of
    dicts ``{'group': VariantGroup, 'required': bool, 'source': 'category'
    |'product'}``. Reads relations via the related managers so a caller that
    prefetched ``category__variant_groups__group__options`` and
    ``variant_group_overrides__group__options`` (the items endpoint) stays
    N+1-free.
    """
    group_objs = {}        # group_id -> VariantGroup
    cat_required = {}      # group_id -> is_required_override (may be None)
    if item.category_id:
        for cvg in item.category.variant_groups.all():
            group_objs[cvg.group_id] = cvg.group
            cat_required[cvg.group_id] = cvg.is_required_override
    prod_overrides = {}    # group_id -> (enabled, is_required_override)
    for pvg in item.variant_group_overrides.all():
        group_objs[pvg.group_id] = pvg.group
        prod_overrides[pvg.group_id] = (pvg.enabled, pvg.is_required_override)

    effective_ids = set()
    for gid in cat_required:
        enabled, _ = prod_overrides.get(gid, (True, None))
        if enabled:
            effective_ids.add(gid)
    for gid, (enabled, _) in prod_overrides.items():
        if enabled:
            effective_ids.add(gid)

    resolved = []
    for gid in effective_ids:
        group = group_objs[gid]
        if not group.is_active:
            continue
        required = group.is_required
        if cat_required.get(gid) is not None:
            required = cat_required[gid]
        if gid in prod_overrides and prod_overrides[gid][1] is not None:
            required = prod_overrides[gid][1]
        source = 'product' if gid in prod_overrides else 'category'
        resolved.append({'group': group, 'required': required, 'source': source})

    resolved.sort(key=lambda r: (r['group'].sort_order, r['group'].name))
    return resolved


def _items_for_group(group_id):
    """Set of Item ids on which the given variant group is effective.

    Mirrors resolve_effective_variant_groups at the group level: category
    assignment provides the base set (all items in the assigned categories),
    product overrides then disable or enable specific items.
    """
    from .models import Item, CategoryVariantGroup, ProductVariantGroup
    cat_ids = list(
        CategoryVariantGroup.objects.filter(group_id=group_id)
        .values_list('category_id', flat=True)
    )
    item_ids = set(
        Item.objects.filter(category_id__in=cat_ids).values_list('id', flat=True)
    ) if cat_ids else set()
    for pid, enabled in ProductVariantGroup.objects.filter(
        group_id=group_id
    ).values_list('product_id', 'enabled'):
        if enabled:
            item_ids.add(pid)
        else:
            item_ids.discard(pid)
    return item_ids


def variant_ingredient_conflict(variant_option, ingredient, exclude_pk=None):
    """FLAG-050: would adding ``ingredient`` to ``variant_option``'s recipe make
    the same ingredient resolve from two different variant groups on one sale?

    At sale time, every selected variant option's recipe is depleted
    (services._deplete_ingredients). If two options from *different* groups both
    contribute the same ingredient and both can be selected on one transaction
    (i.e. both groups are effective on a shared item), the quantities silently
    add. This detects that overlap at authoring time so it can be rejected.

    Returns the conflicting VariantOption, or None when there is no overlap.
    """
    group_id = variant_option.group_id
    my_items = _items_for_group(group_id)
    if not my_items:
        return None
    candidates = (
        RecipeIngredient.objects
        .filter(variant__isnull=False, ingredient=ingredient)
        .exclude(variant__group_id=group_id)
        .select_related('variant', 'variant__group')
    )
    if exclude_pk is not None:
        candidates = candidates.exclude(pk=exclude_pk)
    for recipe in candidates:
        if my_items & _items_for_group(recipe.variant.group_id):
            return recipe.variant
    return None


_VALID_PAYMENT_METHODS = {'cash', 'card', 'gcash', 'maya'}
_PAYMENT_CENTS = Decimal('0.01')


def _resolve_payment_lines(payment_lines, fallback_method, charged_total):
    """FEATURE-016: normalise the requested payment lines for a sale.

    ``payment_lines`` is an optional list of ``{method, amount}``. When absent
    or empty, a single line for ``fallback_method`` covering the whole charged
    total is synthesised (backward compat — every transaction ends up with at
    least one line). The amounts represent actual tender and must sum to the
    charged total (net_total) within ±1 centavo of rounding tolerance.

    Returns ``(lines, primary_method)`` where ``lines`` is a list of
    ``(method, Decimal amount)`` and ``primary_method`` is the method of the
    largest line (first on a tie) — persisted as PosTransaction.payment_method.
    Raises DRFValidationError on any invalid method, non-positive amount, or a
    sum that does not match the charged total.
    """
    charged_total = Decimal(str(charged_total)).quantize(
        _PAYMENT_CENTS, rounding=ROUND_HALF_UP
    )
    if not payment_lines:
        method = fallback_method if fallback_method in _VALID_PAYMENT_METHODS else 'cash'
        return [(method, charged_total)], method

    parsed = []
    for line in payment_lines:
        method = line.get('method')
        if method not in _VALID_PAYMENT_METHODS:
            raise DRFValidationError(f"Invalid payment method: {method!r}.")
        try:
            amount = Decimal(str(line.get('amount'))).quantize(
                _PAYMENT_CENTS, rounding=ROUND_HALF_UP
            )
        except (TypeError, ArithmeticError, ValueError):
            raise DRFValidationError(f"Invalid payment amount: {line.get('amount')!r}.")
        if amount <= 0:
            raise DRFValidationError("Payment line amounts must be greater than zero.")
        parsed.append((method, amount))

    total = sum((a for _, a in parsed), Decimal('0.00'))
    if abs(total - charged_total) > _PAYMENT_CENTS:
        raise DRFValidationError(
            f"Split payment total ({format_currency(total)}) must equal the "
            f"amount due ({format_currency(charged_total)})."
        )

    primary_method = max(parsed, key=lambda p: p[1])[0]
    return parsed, primary_method


def create_pos_transaction(items_data, payment_method, cashier=None, **kwargs):
    """
    Service function to create a POS transaction, its items, and update inventory.
    Expects items_data as list of dicts: [{'item_id' or 'id': ..., 'quantity': ...}]
    """
    with db_transaction.atomic():
        if not items_data:
            raise DRFValidationError("Transaction must contain at least one item.")

        # ISSUE-104: enforce open-shift requirement before any sale work.
        # Defense-in-depth backstop; the frontend prompts first.
        _enforced_shift = Shift.objects.filter(
            cashier=cashier, is_open=True
        ).order_by('-opened_at').first() if cashier else None
        if not _enforced_shift:
            raise DRFValidationError(
                "No open shift. Open a shift before ringing sales."
            )
        # #8 shift-span countermeasure: a shift must not span calendar days
        # (PROD trades 11:00-22:00, so a shift always opens and closes the same
        # PHT day). A shift left open from a previous day mis-dates its Z
        # (business_date = shift OPEN date) and muddies drawer reconciliation.
        # Block further sales until it is closed, so the cashier finalizes
        # yesterday's Z with a real cash count and opens a fresh shift.
        from django.utils import timezone as _dj_tz
        _opened_day = _dj_tz.localdate(_enforced_shift.opened_at)
        if _opened_day < _dj_tz.localdate():
            raise DRFValidationError(
                "This shift has been open since %s. Close it (finalize the "
                "Z-report with a cash count) and open a new shift before "
                "ringing today's sales." % _opened_day.strftime('%b %d')
            )
        from .models import BusinessProfile
        _bp = BusinessProfile.objects.first()
        _track_inventory = not _bp or _bp.track_inventory
        # #3 depletion fix: ingredient depletion for RECIPE items is gated on
        # ingredient management (the recipes/ingredients feature), NOT on
        # track_inventory (which governs item.stock for pure RESALE goods).
        # These were previously coupled under one flag, so with track_inventory
        # off NO sale depleted any ingredient. A recipe item is limited by its
        # ingredients, so it must never be blocked or decremented on item.stock
        # (recipe items carry stock=0) — it depletes its ingredients instead.
        _ingredient_mgmt = not _bp or _bp.ingredient_management_enabled

        # Calculate total and validate stock up front
        total = Decimal('0.00')
        # FEATURE-034: running sum of line subtotals for zero-rated items.
        # Booked to PosTransaction.zero_rated_sales and excluded from the
        # VAT-able base so zero-rated goods carry no output VAT.
        zero_rated_subtotal = Decimal('0.00')
        processed_items = []

        for item_entry in items_data:
            item_id = item_entry.get('item_id') or item_entry.get('id')
            quantity = item_entry.get('quantity', 0)

            # Select item for update to prevent race conditions
            item = Item.objects.select_for_update().get(id=item_id)

            is_recipe_item = item.recipe_ingredients.exists()
            # Only pure resale items (no recipe) are limited by item.stock; a
            # recipe item is limited by its ingredients, checked at depletion.
            if _track_inventory and not is_recipe_item and item.stock < quantity:
                raise ValidationError(f"Insufficient stock for: {item.name}")

            # Resolve variant selections for this item
            variant_selections = item_entry.get('variant_selections', [])  # list of {group_id, option_id}
            base_price = Decimal(str(item.price))
            modifier_total = Decimal('0.00')
            resolved_variants = []

            # ISSUE-073: effective groups + required-variant validation must
            # run unconditionally. Gating them behind `if variant_selections:`
            # let a caller bypass enforcement by omitting the key entirely —
            # a transaction missing a required variant could then be saved.
            # FLAG-049: effective groups + required map come from the single
            # canonical resolver shared with the product API serializer.
            resolved_groups = resolve_effective_variant_groups(item)
            effective_groups = {r['group'].id: r['group'] for r in resolved_groups}
            required_map = {r['group'].id: r['required'] for r in resolved_groups}

            # Validate required groups have a selection. Runs even when
            # variant_selections is absent/empty, so an item with required
            # groups cannot be rung up without those selections (ISSUE-073).
            selected_group_ids = {sel.get('group_id') for sel in variant_selections}
            for gid, group in effective_groups.items():
                if required_map[gid] and str(gid) not in {str(s) for s in selected_group_ids}:
                    raise DRFValidationError(f"'{group.name}' selection is required for {item.name}.")

            if variant_selections:
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

            # FEATURE-010: multi-select cardinality (min/max). Counts per group
            # come from the resolved selections. min/max are ignored for single
            # groups (those are already capped at exactly one above). Each bound
            # is enforced only when set (null means unconstrained).
            for gid, group in effective_groups.items():
                if group.selection_type != 'multi':
                    continue
                selected_count = sum(
                    1 for r in resolved_variants if r['group_id'] == gid
                )
                if group.min_selections is not None and selected_count < group.min_selections:
                    raise DRFValidationError(
                        f"'{group.name}' requires at least {group.min_selections} "
                        f"selection(s) for {item.name}."
                    )
                if group.max_selections is not None and selected_count > group.max_selections:
                    raise DRFValidationError(
                        f"'{group.name}' allows at most {group.max_selections} "
                        f"selection(s) for {item.name}."
                    )

            final_unit_price = base_price + modifier_total
            subtotal = final_unit_price * Decimal(str(quantity))
            total += subtotal
            # FEATURE-034: zero-rated lines accumulate into zero_rated_sales.
            if item.zero_rated:
                zero_rated_subtotal += subtotal

            # FEATURE-054: freeze the effective per-unit COGS at sale time —
            # the recipe-derived cost for recipe items, else the manual
            # purchase_price. Read-time reports can't reconstruct this once
            # ingredient costs drift, so it's snapshotted per line. Includes
            # the SELECTED variants' recipe lines (depletion parity) — an item
            # whose whole recipe is variant-scoped costs 0 through the base-
            # only item_recipe_cost.
            _recipe_cost = item_effective_unit_cost(
                item,
                [rv.get('option_id') for rv in resolved_variants if rv.get('option_id')],
            )
            _unit_cost = _recipe_cost if _recipe_cost is not None else item.purchase_price

            processed_items.append({
                'item': item,
                'is_recipe_item': is_recipe_item,
                'quantity': quantity,
                'unit_price': final_unit_price,   # backward compat: unit_price = final_price
                'base_price': base_price,
                'final_price': final_unit_price,
                'purchase_price': item.purchase_price,
                'unit_cost': _unit_cost,
                'subtotal': subtotal,
                'resolved_variants': resolved_variants,
            })

        # Apply discount — server-side validation
        discount_amount = kwargs.get('discount_amount', 0)
        discount_decimal = Decimal(str(discount_amount)) if discount_amount else Decimal('0.00')
        discount_type = kwargs.get('discount_type', 'none')
        _sc_pwd_vat_amount = Decimal('0.00')
        _is_vat_exempt = False

        _ccode = _bp.currency if _bp else 'PHP'
        if discount_decimal > total:
            raise DRFValidationError(
                f"Discount ({format_currency(discount_decimal, _ccode)}) cannot exceed order total ({format_currency(total, _ccode)})"
            )

        # Re-derive expected discount from BusinessProfile rates
        if discount_decimal > Decimal('0.00') and discount_type not in ('', 'none'):
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
                    f"Discount amount ({format_currency(discount_decimal, _ccode)}) exceeds the allowed maximum "
                    f"({format_currency(expected, _ccode)}) for discount type '{discount_type}'."
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

        # FEATURE-012: freeze totals at commit. Single source of truth from
        # this point forward. gross_total carries VAT-inclusive semantics only
        # when BusinessProfile.vat_inclusive (it is just the pre-discount sum
        # of line subtotals — the prices already embed VAT in inclusive mode).
        _q = Decimal('0.01')
        _frozen_gross = total.quantize(_q, rounding=ROUND_HALF_UP)
        _frozen_discount = discount_decimal.quantize(_q, rounding=ROUND_HALF_UP)
        _frozen_net = final_total.quantize(_q, rounding=ROUND_HALF_UP)
        # FEATURE-034: zero-rated bucket — full sale amount of zero-rated lines.
        _frozen_zero_rated = zero_rated_subtotal.quantize(_q, rounding=ROUND_HALF_UP)
        _vat_enabled = bool(_bp and _bp.vat_enabled)
        # Null-aware VAT rate read from BusinessProfile — never a literal 12 / 0.12.
        _vat_rate = (
            Decimal(str(_bp.vat_rate)) if (_bp is not None and _bp.vat_rate is not None)
            else Decimal('0')
        )
        if _is_vat_exempt:
            # SC/PWD: VAT was removed (_sc_pwd_vat_amount); the charged amount
            # is entirely VAT-exempt sales. A zero-rated flag does not stack on
            # top of an SC/PWD exemption — the exemption already removed VAT.
            _frozen_vat_exempt = _frozen_net
            _frozen_vatable = Decimal('0.00')
            _frozen_zero_rated = Decimal('0.00')
        elif _vat_enabled and _vat_rate > 0:
            _frozen_vat_exempt = Decimal('0.00')
            # FEATURE-034: zero-rated sales carry no output VAT. Remove their
            # gross from the VAT-able base before extracting output VAT.
            _vatable_charged = _frozen_net - _frozen_zero_rated
            if _vatable_charged < 0:
                _vatable_charged = Decimal('0.00')
            _vat_inclusive = bool(_bp and _bp.vat_inclusive)
            if _vat_inclusive:
                _output_vat = (
                    _vatable_charged * _vat_rate / (Decimal('100') + _vat_rate)
                ).quantize(_q, rounding=ROUND_HALF_UP)
                _frozen_vatable = (_vatable_charged - _output_vat).quantize(
                    _q, rounding=ROUND_HALF_UP
                )
            else:
                _frozen_vatable = _vatable_charged.quantize(
                    _q, rounding=ROUND_HALF_UP
                )
        else:
            # VAT disabled — no VAT breakdown applies.
            _frozen_vat_exempt = Decimal('0.00')
            _frozen_vatable = Decimal('0.00')

        # Bind the transaction to the open shift enforced at the top of
        # this atomic block (ISSUE-104).
        current_shift = _enforced_shift

        # FEATURE-016: resolve payment lines (split payment support). The
        # amounts represent actual tender and must sum to the charged total
        # (final_total / net_total). primary_method is persisted as the
        # PosTransaction.payment_method for backward compat.
        payment_lines, primary_method = _resolve_payment_lines(
            kwargs.get('payment_lines'), payment_method, final_total
        )

        # Create transaction
        cash_received = kwargs.get('cash_received')
        cash_received_amount = Decimal(str(cash_received)) if cash_received else None
        change_given = (cash_received_amount - final_total).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        ) if cash_received_amount else None

        transaction = PosTransaction.objects.create(
            total_amount=Money(final_total, 'PHP'),
            discount_amount=discount_decimal,
            discount_type=kwargs.get('discount_type', 'none'),
            discount_id_number=kwargs.get('discount_id_number', ''),
            vat_exempt=_is_vat_exempt,
            vat_amount=_sc_pwd_vat_amount,
            gross_total=_frozen_gross,
            discount_total=_frozen_discount,
            vat_exempt_amount=_frozen_vat_exempt,
            vatable_sales=_frozen_vatable,
            zero_rated_sales=_frozen_zero_rated,
            net_total=_frozen_net,
            status='completed',
            payment_method=primary_method,
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
                unit_cost=entry['unit_cost'],
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

            # Resale items (no recipe): decrement tracked item.stock.
            if _track_inventory and not entry['is_recipe_item']:
                # Atomic check-and-decrement — single UPDATE WHERE, SQLite safe
                updated = Item.objects.filter(
                    pk=item.pk,
                    stock__gte=entry['quantity']
                ).update(stock=F('stock') - entry['quantity'])
                if updated == 0:
                    raise ValidationError(
                        f"Insufficient stock for: {item.name} (sold out during checkout)"
                    )

            # Recipe items: deplete ingredients + write the ledger (ISSUE-069),
            # gated on ingredient management, independent of track_inventory so a
            # recipe sale always records real consumption. Attributed to the
            # ringing cashier and linked to this transaction.
            if _ingredient_mgmt and entry['is_recipe_item']:
                variant_option_ids = [
                    rv.get('option_id') for rv in entry.get('resolved_variants', [])
                    if rv.get('option_id')
                ]
                _deplete_ingredients(
                    item, variant_option_ids, entry['quantity'],
                    transaction=transaction, performed_by=cashier,
                )

        # FEATURE-016: persist the tender breakdown. Every sale gets at least
        # one PaymentLine; split payments get one per method.
        PaymentLine.objects.bulk_create([
            PaymentLine(transaction=transaction, method=method, amount=amount)
            for method, amount in payment_lines
        ])

        # BUG-009: the receipt print is NO LONGER triggered here. This thread
        # was started INSIDE the atomic block (before commit), so its separate
        # DB connection raced the uncommitted transaction — under SQLite it
        # could read a locked/empty row and silently fail (daemon-thread
        # exceptions are swallowed), making auto-print intermittent. The print
        # is now triggered by the frontend AFTER the sale POST returns (post
        # commit), routed through authenticatedFetch's single-flight refresh so
        # a token rotation retries instead of dropping, with a visible failure
        # toast. receipt_service / the print mechanism itself is untouched.
        # The cashbox kick has no such data dependency, so it stays here.
        threading.Thread(target=kick_cash_drawer, daemon=True).start()
        return transaction


@db_transaction.atomic
def refund_transaction(original_id, performed_by):
    """FEATURE-015: issue a refund for a prior sale.

    A refund leaves the original transaction untouched and immutable and posts
    a NEW negative PosTransaction (transaction_type='refund', refund_of=
    original) to the refunder's current open shift — so the refund's financial
    impact lands in the shift it is processed, not retroactively on the
    original's Z. Ingredient stock is restored via the IngredientLog ledger
    (action='refund'). Returns the new refund transaction.

    Raises DRFValidationError when the original is a refund, already voided,
    already refunded, or when the refunder has no open shift.
    """
    original = PosTransaction.objects.select_for_update().get(pk=original_id)

    if original.transaction_type == 'refund':
        raise DRFValidationError("A refund transaction cannot itself be refunded.")
    if original.void or original.status == 'void':
        raise DRFValidationError("A voided transaction cannot be refunded.")
    if original.refunds.exists():
        raise DRFValidationError("This transaction has already been refunded.")

    shift = Shift.objects.filter(
        cashier=performed_by, is_open=True
    ).order_by('-opened_at').first() if performed_by else None
    if not shift:
        raise DRFValidationError(
            "No open shift. Open a shift before issuing refunds."
        )

    def _neg(value):
        return -(Decimal(str(value))) if value is not None else Decimal('0.00')

    refund = PosTransaction.objects.create(
        total_amount=Money(_neg(original.net_total), 'PHP'),
        transaction_type='refund',
        refund_of=original,
        status='refunded',
        payment_method=original.payment_method,
        # Mirror the frozen totals as negatives so every downstream aggregate
        # (X/Z sales, payment breakdown, cash reconciliation) nets correctly.
        gross_total=_neg(original.gross_total),
        net_total=_neg(original.net_total),
        discount_total=_neg(original.discount_total),
        discount_amount=_neg(original.discount_amount),
        vat_amount=_neg(original.vat_amount),
        vat_exempt_amount=_neg(original.vat_exempt_amount),
        vatable_sales=_neg(original.vatable_sales),
        zero_rated_sales=_neg(original.zero_rated_sales),
        vat_exempt=original.vat_exempt,
        discount_type=original.discount_type,
        cashier=performed_by,
        shift=shift,
        is_seed=False,
    )

    # Restore ingredient stock (action='refund'). Item.stock is intentionally
    # left untouched — FEATURE-015 scopes the refund restore to the ingredient
    # ledger only.
    from .models import BusinessProfile
    _bp = BusinessProfile.objects.first()
    # Gated on ingredient management (mirrors the sale-path depletion), not
    # track_inventory; item.stock is intentionally left untouched here.
    if not _bp or _bp.ingredient_management_enabled:
        for item_entry in original.items.all():
            _restore_ingredients(
                item_entry.item, item_entry, item_entry.quantity,
                transaction=refund, performed_by=performed_by, action='refund',
            )

    return refund


_Z_CENTS = Decimal('0.01')


def _zsum(queryset, field):
    """Decimal sum of a frozen PosTransaction column, 2dp, never None."""
    from django.db.models import Sum
    total = queryset.aggregate(_s=Sum(field))['_s']
    return (Decimal(str(total)) if total is not None else Decimal('0')).quantize(
        _Z_CENTS, rounding=ROUND_HALF_UP
    )


@db_transaction.atomic
def close_shift_and_finalize_z(shift_id, cash_counted, cashier_user):
    """Finalize a shift into an immutable Z report.

    Locks the Shift + ZCounter, aggregates the frozen PosTransaction
    columns (read-only), snapshots BusinessProfile identity, creates the
    ZReport, marks the shift closed. Returns the new ZReport.

    Raises:
      - Shift.DoesNotExist if shift_id not found
      - ValidationError if the shift is already closed

    ISSUE-105: a blank BusinessProfile MIN no longer blocks finalize.
    The resulting Z is flagged is_official=False (UNOFFICIAL) so
    pre-BIR-accreditation cafes can still close shifts.
    """
    from django.utils import timezone as dj_tz
    from .models import (
        BusinessProfile, PosTransaction, Shift, ZCounter, ZReport,
    )

    # Lock the shift row first.
    shift = Shift.objects.select_for_update().get(pk=shift_id)
    if shift.closed_at or not shift.is_open:
        raise ValidationError("Shift is already closed.")

    # ISSUE-105: no MIN gate. A blank MIN yields an UNOFFICIAL Z.
    bp = BusinessProfile.objects.first()
    is_official = bool(bp and (bp.machine_identification_number or '').strip())

    # FLAG-047: seed/demo rows are excluded from every Z aggregate so a
    # quarantined demo transaction can never leak into a BIR-grade Z total.
    # FEATURE-015: refunds are negative transactions posted to this shift. They
    # are kept OUT of the sales aggregates (which must stay pure sales for BIR
    # gross/net sales) and surfaced on a dedicated "Refunds" deduction line.
    non_voided = PosTransaction.objects.filter(
        shift=shift, voided_at__isnull=True, is_seed=False,
    ).exclude(transaction_type='refund')
    voided = PosTransaction.objects.filter(
        shift=shift, voided_at__isnull=False, is_seed=False,
    ).exclude(transaction_type='refund')
    refunds = PosTransaction.objects.filter(
        shift=shift, is_seed=False, transaction_type='refund',
    )

    gross_sales = _zsum(non_voided, 'gross_total')
    discount_total = _zsum(non_voided, 'discount_total')
    net_sales = _zsum(non_voided, 'net_total')
    vatable_sales = _zsum(non_voided, 'vatable_sales')
    vat_exempt_sales = _zsum(non_voided, 'vat_exempt_amount')
    zero_rated_sales = _zsum(non_voided, 'zero_rated_sales')

    # output_vat = sum(net_total - vatable_sales - zero_rated_sales) over
    # non-exempt rows (a row is "exempt" when it carries a vat_exempt_amount).
    # FEATURE-034: zero-rated sales are VAT-inclusive in net_total but carry no
    # output VAT, so they must be subtracted out alongside vatable_sales.
    non_exempt = non_voided.filter(vat_exempt_amount=Decimal('0'))
    output_vat = (
        _zsum(non_exempt, 'net_total') - _zsum(non_exempt, 'vatable_sales')
        - _zsum(non_exempt, 'zero_rated_sales')
    ).quantize(_Z_CENTS, rounding=ROUND_HALF_UP)

    sc_discount_total = _zsum(
        non_voided.filter(discount_type='sc'), 'discount_total'
    )
    pwd_discount_total = _zsum(
        non_voided.filter(discount_type='pwd'), 'discount_total'
    )
    promo_discount_total = _zsum(
        non_voided.filter(discount_type='promo'), 'discount_total'
    )

    # FEATURE-016: payment breakdown + cash reconciliation come from the
    # PaymentLine tender rows (grouped by method) of the sale transactions, not
    # the single PosTransaction.payment_method. Refunds carry no PaymentLines,
    # so they are handled separately below.
    from django.db.models import Sum
    from .models import PaymentLine
    pl_rows = (
        PaymentLine.objects.filter(transaction__in=non_voided)
        .values('method')
        .annotate(total=Sum('amount'))
    )
    pl_by_method = {r['method']: r['total'] for r in pl_rows}

    def _pl(method):
        amt = pl_by_method.get(method)
        return (Decimal(str(amt)) if amt is not None else Decimal('0')).quantize(
            _Z_CENTS, rounding=ROUND_HALF_UP
        )

    payment_breakdown = {}
    for method in ['cash', 'gcash', 'maya', 'card']:
        payment_breakdown[method] = str(_pl(method))

    # FEATURE-015: refund aggregates. refund_total is a positive magnitude
    # (refund rows carry negative gross_total, so negate the sum).
    refund_count = refunds.count()
    refund_total = (-_zsum(refunds, 'gross_total')).quantize(
        _Z_CENTS, rounding=ROUND_HALF_UP
    )
    # Cash paid out on refunds (refund cash net_total is negative).
    refund_cash = (-_zsum(
        refunds.filter(payment_method='cash'), 'net_total'
    )).quantize(_Z_CENTS, rounding=ROUND_HALF_UP)

    cash_collected = _pl('cash')
    opening_cash = (
        Decimal(str(shift.opening_cash or 0))
    ).quantize(_Z_CENTS, rounding=ROUND_HALF_UP)
    # Refunded cash left the drawer this shift, so it reduces what we expect.
    cash_expected = (opening_cash + cash_collected - refund_cash).quantize(
        _Z_CENTS, rounding=ROUND_HALF_UP
    )
    counted = (
        Decimal(str(cash_counted)).quantize(_Z_CENTS, rounding=ROUND_HALF_UP)
        if cash_counted is not None else None
    )
    over_short = (
        (counted - cash_expected).quantize(_Z_CENTS, rounding=ROUND_HALF_UP)
        if counted is not None else None
    )

    # OR range within the shift.
    ordered = non_voided.order_by('created_at')
    first_txn = ordered.first()
    last_txn = ordered.last()
    voided_or_numbers = [
        n for n in voided.order_by('created_at').values_list(
            'transaction_no', flat=True
        ) if n
    ]

    # Gapless Z numbering + running grand total (locked singleton).
    counter, _ = ZCounter.objects.select_for_update().get_or_create(pk=1)
    # FEATURE-035: once BIR accreditation has been applied, every Z in the
    # official series is is_official regardless of the live MIN read.
    if counter.accredited_at:
        is_official = True
    next_z = counter.z_counter + 1
    next_reset = counter.reset_counter
    if next_z > 9999:
        next_z = 1
        next_reset = counter.reset_counter + 1
    counter.z_counter = next_z
    counter.reset_counter = next_reset
    counter.grand_total = (
        Decimal(str(counter.grand_total or 0)) + gross_sales
    ).quantize(_Z_CENTS, rounding=ROUND_HALF_UP)
    counter.save()

    z_report = ZReport.objects.create(
        z_counter=next_z,
        reset_counter=next_reset,
        business_date=dj_tz.localdate(shift.opened_at),
        started_at=shift.opened_at,
        shift=shift,
        business_name=bp.business_name or '',
        business_tin=bp.tin or '',
        business_address=bp.address or '',
        machine_identification_number=bp.machine_identification_number or '',
        machine_serial_number=bp.machine_serial_number or '',
        pos_accreditation_number=bp.pos_accreditation_number or '',
        pos_permit_number=bp.pos_permit_number or '',
        first_or_number=(first_txn.transaction_no if first_txn else ''),
        last_or_number=(last_txn.transaction_no if last_txn else ''),
        voided_or_numbers=voided_or_numbers,
        transaction_count=non_voided.count(),
        voided_count=voided.count(),
        refund_count=refund_count,
        refund_total=refund_total,
        gross_sales=gross_sales,
        discount_total=discount_total,
        net_sales=net_sales,
        vatable_sales=vatable_sales,
        vat_exempt_sales=vat_exempt_sales,
        zero_rated_sales=zero_rated_sales,
        output_vat=output_vat,
        sc_discount_total=sc_discount_total,
        pwd_discount_total=pwd_discount_total,
        promo_discount_total=promo_discount_total,
        payment_breakdown=payment_breakdown,
        opening_cash=opening_cash,
        cash_collected=cash_collected,
        cash_expected=cash_expected,
        cash_counted=counted,
        over_short=over_short,
        grand_total_sales=counter.grand_total,
        currency=(bp.currency or 'PHP'),
        is_official=is_official,
        cashier=cashier_user,
    )

    shift.closed_at = dj_tz.now()
    if counted is not None:
        shift.closing_cash = counted
    shift.is_open = False
    shift.save()

    return z_report


class AccreditationAlreadyApplied(Exception):
    """FEATURE-035: raised when an accreditation reset is attempted twice."""

    def __init__(self, accredited_at):
        self.accredited_at = accredited_at
        super().__init__(
            f"Accreditation reset already applied on {accredited_at.date()}"
        )


@db_transaction.atomic
def apply_accreditation_reset(user):
    """FEATURE-035: restart the official Z-series at #1 for BIR accreditation.

    Locks the singleton ZCounter, stamps the accreditation event on it,
    zeroes the Z counter and running grand total, and flags every existing
    (pre-accreditation) ZReport is_official=False. Idempotent guard: a second
    call raises AccreditationAlreadyApplied.

    Returns the locked ZCounter. The next close_shift_and_finalize_z produces
    z_counter=1, is_official=True.
    """
    from .models import ZCounter, ZReport
    from django.utils import timezone as dj_tz

    counter, _ = ZCounter.objects.select_for_update().get_or_create(pk=1)
    if counter.accredited_at:
        raise AccreditationAlreadyApplied(counter.accredited_at)

    now = dj_tz.now()
    counter.accredited_at = now
    counter.reset_by = user
    counter.z_counter = 0
    counter.grand_total = Decimal('0')
    # Start a fresh reset series so the official Z-1 does not collide with the
    # retained pre-accreditation Z-1 (z_counter is unique per reset series).
    counter.reset_counter = counter.reset_counter + 1
    counter.save()

    # Every existing ZReport is pre-accreditation. ZReport.save() rejects
    # re-saves (immutable), so use a bulk queryset UPDATE to flag them without
    # tripping that guard.
    ZReport.objects.all().update(is_official=False)

    return counter
