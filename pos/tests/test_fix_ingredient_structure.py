"""Tests for the PROD structural ingredient/recipe dedup command (2026-07-18).

Pins: recipes repoint off the retired copy onto the kept copy; active flags flip
the right way; a dry run writes nothing; guards abort on the wrong record; and the
sales record is provably untouched.
"""
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from pos.models import (
    Ingredient, IngredientUnit, Item, RecipeIngredient,
)


class FixIngredientStructureTests(TestCase):
    def setUp(self):
        self.unit, _ = IngredientUnit.objects.get_or_create(
            abbreviation='scoop', defaults={'name': 'Scoop'})
        # Fruit jelly: retire=45 (has the recipes, inactive), keep=94 (active, empty)
        self.jelly_retire = Ingredient.objects.create(
            pk=45, name='Fruit jelly', unit=self.unit,
            cost_per_unit=Decimal('2.5406'), current_stock=Decimal('186'),
            is_active=False)
        self.jelly_keep = Ingredient.objects.create(
            pk=94, name='Fruit jelly', unit=self.unit,
            cost_per_unit=Decimal('2.59'), current_stock=Decimal('84'),
            is_active=True)
        # Choco mousse: keep=34 (has recipes, INACTIVE), retire=95 (active, empty)
        self.choco_keep = Ingredient.objects.create(
            pk=34, name='Choco mousse', unit=self.unit,
            cost_per_unit=Decimal('0.8063'), current_stock=Decimal('0'),
            is_active=False)
        self.choco_retire = Ingredient.objects.create(
            pk=95, name='Choco mousse', unit=self.unit,
            cost_per_unit=Decimal('0'), current_stock=Decimal('0'),
            is_active=True)
        # 12 fruit-jelly recipe lines all on the retired copy.
        self.items = []
        for i in range(12):
            it = Item.objects.create(name=f'Fruit Tea {i}', price=Decimal('59'), stock=0)
            RecipeIngredient.objects.create(
                item=it, ingredient=self.jelly_retire, quantity_used=Decimal('0.5'))
            self.items.append(it)
        # 2 choco recipe lines already on the kept (inactive) copy.
        choco_item = Item.objects.create(name='Milk Tea Choco', price=Decimal('69'), stock=0)
        for q in ('2.5', '3'):
            RecipeIngredient.objects.create(
                item=choco_item, ingredient=self.choco_keep, quantity_used=Decimal(q))

    def test_dry_run_writes_nothing(self):
        call_command('fix_ingredient_structure')  # no --apply
        self.assertEqual(
            RecipeIngredient.objects.filter(ingredient=self.jelly_retire).count(), 12)
        self.choco_keep.refresh_from_db()
        self.assertFalse(self.choco_keep.is_active)  # unchanged

    def test_apply_repoints_and_flips_flags(self):
        call_command('fix_ingredient_structure', apply=True)
        # all 12 jelly lines moved to the kept copy
        self.assertEqual(
            RecipeIngredient.objects.filter(ingredient=self.jelly_retire).count(), 0)
        self.assertEqual(
            RecipeIngredient.objects.filter(ingredient=self.jelly_keep).count(), 12)
        # active flags
        for ing, expected in (
                (self.choco_keep, True), (self.choco_retire, False),
                (self.jelly_retire, False), (self.jelly_keep, True)):
            ing.refresh_from_db()
            self.assertEqual(ing.is_active, expected, f'id={ing.pk}')
        # choco recipes untouched (still on the kept copy), costs untouched
        self.assertEqual(
            RecipeIngredient.objects.filter(ingredient=self.choco_keep).count(), 2)
        self.choco_keep.refresh_from_db()
        self.assertEqual(self.choco_keep.cost_per_unit, Decimal('0.8063'))  # NOT changed

    def test_idempotent(self):
        call_command('fix_ingredient_structure', apply=True)
        # second apply: jelly_retire now has 0 recipes -> repoint 0, flags already set
        call_command('fix_ingredient_structure', apply=True)
        self.assertEqual(
            RecipeIngredient.objects.filter(ingredient=self.jelly_keep).count(), 12)

    def test_guard_aborts_on_wrong_record(self):
        # point --choco-keep at a fruit-jelly id -> name guard must abort
        with self.assertRaises(CommandError):
            call_command('fix_ingredient_structure', apply=True, choco_keep=94)
        # nothing changed
        self.assertEqual(
            RecipeIngredient.objects.filter(ingredient=self.jelly_retire).count(), 12)
