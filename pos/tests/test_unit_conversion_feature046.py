"""FEATURE-046 / ISSUE-122 — recipe quantities entered in a working unit.

Recipe lines have always been stored in the ingredient's BASE unit
(ISSUE-113), so someone writing a recipe had to convert "one scoop" into grams
in their head. This lets the recipe say "1 scoop" and converts on the way in.

🔴 The invariant these tests exist to protect: quantity_used REMAINS the single
source of truth, always in base units. Every depletion and costing path reads
it and none of them knows about entry units. If a conversion bug could reach
quantity_used unnoticed, it would silently change what a sale depletes and what
a recipe costs — so the conversion fails LOUDLY when undefined rather than
assuming 1:1.

Conversions are per-INGREDIENT on purpose: a scoop of matcha and a scoop of
sugar are different masses.
"""

from decimal import Decimal

from django.test import TestCase

from pos.models import (
    Ingredient, IngredientUnit, IngredientUnitConversion, Item,
    RecipeIngredient,
)
from pos.services import UnitConversionError, convert_to_base_units


class ConversionTests(TestCase):
    def setUp(self):
        self.g = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})[0]
        self.scoop = IngredientUnit.objects.get_or_create(
            abbreviation='scoop', defaults={'name': 'Scoop'})[0]
        self.ml = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Millilitre'})[0]
        self.matcha = Ingredient.objects.create(
            name='Matcha powder', unit=self.g,
            cost_per_unit=Decimal('2.0000'), current_stock=Decimal('1000'))
        self.sugar = Ingredient.objects.create(
            name='Sugar', unit=self.g,
            cost_per_unit=Decimal('0.1000'), current_stock=Decimal('1000'))

    def test_converts_using_the_ingredients_own_factor(self):
        IngredientUnitConversion.objects.create(
            ingredient=self.matcha, unit=self.scoop,
            to_base_factor=Decimal('2.5'))

        self.assertEqual(
            convert_to_base_units(self.matcha, Decimal('2'), self.scoop),
            Decimal('5.0000'))

    def test_NEGATIVE_CONTROL_same_unit_different_ingredient_differs(self):
        """A scoop of matcha is not a scoop of sugar. If the factor were
        global, these two would agree — and one of them would be wrong."""
        IngredientUnitConversion.objects.create(
            ingredient=self.matcha, unit=self.scoop,
            to_base_factor=Decimal('2.5'))
        IngredientUnitConversion.objects.create(
            ingredient=self.sugar, unit=self.scoop,
            to_base_factor=Decimal('8'))

        self.assertEqual(
            convert_to_base_units(self.matcha, Decimal('1'), self.scoop),
            Decimal('2.5000'))
        self.assertEqual(
            convert_to_base_units(self.sugar, Decimal('1'), self.scoop),
            Decimal('8.0000'))

    def test_base_unit_is_identity(self):
        self.assertEqual(
            convert_to_base_units(self.matcha, Decimal('7'), self.g),
            Decimal('7'))

    def test_none_unit_is_identity(self):
        """Entering directly in base units must keep working untouched."""
        self.assertEqual(
            convert_to_base_units(self.matcha, Decimal('7'), None),
            Decimal('7'))

    def test_PURCHASE_unit_works_with_NO_conversion_row(self):
        """FEATURE-050 already stores a per-ingredient package->base factor and
        the restock form converts with it. Recipes must reuse THAT, not a
        second copy — so an ingredient bought by the sack is usable in a recipe
        by the sack immediately, with no new data entry."""
        sack = IngredientUnit.objects.get_or_create(
            abbreviation='sack', defaults={'name': 'Sack'})[0]
        self.sugar.purchase_unit = sack
        self.sugar.purchase_to_base_factor = Decimal('25000')
        self.sugar.save()

        self.assertEqual(
            convert_to_base_units(self.sugar, Decimal('2'), sack),
            Decimal('50000.0000'))

    def test_cannot_shadow_the_purchase_unit_with_a_conversion_row(self):
        """🔴 Two copies of "1 sack = 25000 g" could be edited apart, after
        which restock and depletion would silently disagree about what a sack
        is. Blocked at write time."""
        from django.core.exceptions import ValidationError
        sack = IngredientUnit.objects.get_or_create(
            abbreviation='sack', defaults={'name': 'Sack'})[0]
        self.sugar.purchase_unit = sack
        self.sugar.purchase_to_base_factor = Decimal('25000')
        self.sugar.save()

        with self.assertRaises(ValidationError):
            IngredientUnitConversion.objects.create(
                ingredient=self.sugar, unit=sack,
                to_base_factor=Decimal('9999'))

    def test_cannot_add_a_conversion_for_the_base_unit(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            IngredientUnitConversion.objects.create(
                ingredient=self.sugar, unit=self.g,
                to_base_factor=Decimal('1'))

    def test_undefined_conversion_FAILS_LOUDLY(self):
        """🔴 The important one. Assuming 1:1 would under-deplete stock by the
        whole real factor, and would only surface weeks later as untraceable
        inventory drift."""
        with self.assertRaises(UnitConversionError):
            convert_to_base_units(self.matcha, Decimal('1'), self.scoop)


class RecipeEntryTests(TestCase):
    def setUp(self):
        self.g = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})[0]
        self.scoop = IngredientUnit.objects.get_or_create(
            abbreviation='scoop', defaults={'name': 'Scoop'})[0]
        self.matcha = Ingredient.objects.create(
            name='Matcha powder', unit=self.g,
            cost_per_unit=Decimal('2.0000'), current_stock=Decimal('1000'))
        self.item = Item.objects.create(
            name='Matcha Latte', price=Decimal('69.00'), stock=100)
        IngredientUnitConversion.objects.create(
            ingredient=self.matcha, unit=self.scoop,
            to_base_factor=Decimal('2.5'))

    def test_entry_fields_derive_quantity_used(self):
        r = RecipeIngredient.objects.create(
            item=self.item, ingredient=self.matcha,
            quantity_used=Decimal('0'),          # will be overwritten
            entry_unit=self.scoop, entry_quantity=Decimal('2'),
        )
        r.refresh_from_db()
        self.assertEqual(r.quantity_used, Decimal('5.0000'))

    def test_NEGATIVE_CONTROL_without_entry_fields_quantity_is_untouched(self):
        """Existing rows and direct base-unit entry must be unaffected —
        this is what keeps every historical recipe correct."""
        r = RecipeIngredient.objects.create(
            item=self.item, ingredient=self.matcha,
            quantity_used=Decimal('12.0000'),
        )
        r.refresh_from_db()
        self.assertEqual(r.quantity_used, Decimal('12.0000'))
        self.assertIsNone(r.entry_unit)
        self.assertIsNone(r.entry_quantity)

    def test_editing_the_entry_requantifies(self):
        r = RecipeIngredient.objects.create(
            item=self.item, ingredient=self.matcha,
            quantity_used=Decimal('0'),
            entry_unit=self.scoop, entry_quantity=Decimal('2'),
        )
        r.entry_quantity = Decimal('4')
        r.save()
        r.refresh_from_db()
        self.assertEqual(r.quantity_used, Decimal('10.0000'))

    def test_undefined_conversion_blocks_the_save(self):
        """Better a refused recipe line than a silently wrong depletion."""
        other = IngredientUnit.objects.get_or_create(
            abbreviation='tbsp', defaults={'name': 'Tablespoon'})[0]
        with self.assertRaises(UnitConversionError):
            RecipeIngredient.objects.create(
                item=self.item, ingredient=self.matcha,
                quantity_used=Decimal('0'),
                entry_unit=other, entry_quantity=Decimal('1'),
            )

    def test_conversion_is_unique_per_ingredient_unit_pair(self):
        # save() calls full_clean(), so the duplicate surfaces as a
        # ValidationError before the DB constraint fires. The constraint is
        # still there as the backstop for raw/bulk writes.
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            IngredientUnitConversion.objects.create(
                ingredient=self.matcha, unit=self.scoop,
                to_base_factor=Decimal('9'))
