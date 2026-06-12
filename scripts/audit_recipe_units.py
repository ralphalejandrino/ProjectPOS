#!/usr/bin/env python3
# B-INVESTIGATE-INV: PROD recipe-unit data audit (READ-ONLY).
#
# Background: the depletion path has NO unit conversion (ISSUE-113 audit,
# ISSUE-122 STOP report) — RecipeIngredient.quantity_used depletes in the
# ingredient's OWN unit. An ingredient tracked in kg with a recipe line
# entered as grams (or vice versa) depletes 1000x off. This script prints
# every recipe line with its ingredient's unit and flags suspicious values
# so Ralph can review entries with the manager. It writes NOTHING.
#
# Usage (on the client box, from the repo root, venv active):
#   python scripts/audit_recipe_units.py
#
# Flags:
#   BIG-FOR-BULK-UNIT   quantity >= 1000 against a kg/l/L ingredient
#                       (probably entered in g/ml)
#   TINY-FOR-SMALL-UNIT quantity < 0.01 against a g/ml ingredient
#                       (probably entered in kg/l)
#   ZERO-OR-NEGATIVE    quantity <= 0 (pre-ISSUE-113 rows; API now rejects)
#   NO-DEPLETION        ingredient has track_depletion=False (FLAG-046) —
#                       this line never moves stock
#   NEGATIVE-STOCK      ingredient stock is negative right now (the
#                       owner-investigate signal; often a unit-entry artifact)

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_config.settings')

import django  # noqa: E402

django.setup()

from canteen.models import Ingredient, RecipeIngredient  # noqa: E402

BULK_UNITS = {'kg', 'l'}        # compared lowercased
SMALL_UNITS = {'g', 'ml'}

BIG_QTY = Decimal('1000')
TINY_QTY = Decimal('0.01')


def flags_for(recipe, unit_abbr):
    qty = recipe.quantity_used
    unit = (unit_abbr or '').lower()
    out = []
    if qty <= 0:
        out.append('ZERO-OR-NEGATIVE')
    if unit in BULK_UNITS and qty >= BIG_QTY:
        out.append('BIG-FOR-BULK-UNIT')
    if unit in SMALL_UNITS and Decimal('0') < qty < TINY_QTY:
        out.append('TINY-FOR-SMALL-UNIT')
    if not recipe.ingredient.track_depletion:
        out.append('NO-DEPLETION')
    return out


def fmt(d):
    """Plain-number Decimal formatting — never scientific notation."""
    s = format(d.normalize(), 'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


def target_name(recipe):
    if recipe.item_id:
        return f"product '{recipe.item.name}'"
    if recipe.variant_id:
        return (f"variant '{recipe.variant.group.name} / "
                f"{recipe.variant.name}'")
    return '(unlinked)'


def main():
    rows = (
        RecipeIngredient.objects
        .select_related('ingredient__unit', 'item', 'variant__group')
        .order_by('ingredient__name')
    )
    print('=' * 78)
    print('PROD RECIPE-UNIT AUDIT (read-only) — review flagged rows with the manager')
    print('=' * 78)
    flagged = 0
    for r in rows:
        unit = r.ingredient.unit.abbreviation if r.ingredient.unit else '?'
        fl = flags_for(r, unit)
        marker = '  <<< ' + ', '.join(fl) if fl else ''
        flagged += bool(fl)
        print(f"{target_name(r)}: {r.ingredient.name} — "
              f"{fmt(r.quantity_used)} {unit} per unit sold"
              f" (ingredient stock: {fmt(r.ingredient.current_stock)} {unit})"
              f"{marker}")
    print('-' * 78)

    negatives = Ingredient.objects.filter(current_stock__lt=0)
    for ing in negatives:
        unit = ing.unit.abbreviation if ing.unit else '?'
        print(f"NEGATIVE-STOCK: {ing.name} is at "
              f"{fmt(ing.current_stock)} {unit}")
    print('-' * 78)
    print(f"recipe lines: {rows.count()}  |  flagged: {flagged}  |  "
          f"negative-stock ingredients: {negatives.count()}")
    print('No data was modified.')


if __name__ == '__main__':
    main()
