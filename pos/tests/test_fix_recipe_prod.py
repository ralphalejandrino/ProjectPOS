"""Tests for the one-time fix_recipe_prod command (PROD recipe/menu correction,
manager-confirmed 2026-07-21). The fixture recreates the live-box state the
command's guards expect (forced pks for Nata/fruit jelly); tests pin the full
end state, dry-run inertness, idempotency, atomic guard aborts, and that sales
records + unrelated ingredients/recipe lines are untouched."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from pos.models import (
    BusinessProfile, CategoryVariantGroup, Ingredient, IngredientUnit, Item,
    ItemCategory, PosTransaction, RecipeIngredient, Shift, User, VariantGroup,
    VariantOption,
)
from pos.services import create_pos_transaction

D = Decimal


class FixRecipeProdTests(TestCase):
    def setUp(self):
        BusinessProfile.objects.create(
            business_name='PROD', currency='PHP', vat_enabled=False,
            track_inventory=True, printer_mode='disabled',
        )
        self.admin = User.objects.create_user(username='admin', password='x', role='admin')
        self.cashier = User.objects.create_user(username='cashier', password='x', role='cashier')
        scoop = IngredientUnit.objects.get_or_create(abbreviation='scoop', defaults={'name': 'scoop'})[0]
        pcs = IngredientUnit.objects.get_or_create(abbreviation='pcs', defaults={'name': 'pieces'})[0]
        self.kg = IngredientUnit.objects.get_or_create(abbreviation='kg', defaults={'name': 'Kilograms'})[0]

        def ing(pk, name, unit, cost, stock, price=None, factor=None, p_unit=None):
            Ingredient.objects.create(
                id=pk, name=name, unit=unit, cost_per_unit=D('0'),
                current_stock=D('0'), purchase_unit=p_unit,
            )
            Ingredient.objects.filter(pk=pk).update(
                cost_per_unit=cost, current_stock=stock,
                last_purchase_price=price, purchase_to_base_factor=factor,
            )
            return Ingredient.objects.get(pk=pk)

        # Fruit jelly = the config template (2.5kg tub -> 108 scoops @ P280).
        self.fj = ing(94, 'Fruit jelly', scoop, D('2.59'), D('79.5'),
                      D('280'), 108, self.kg)
        # Nata = same cost, but NO purchase config yet.
        self.nata = ing(44, 'Nata', scoop, D('2.59'), D('77.5'))
        # Negative-control ingredient — must stay untouched.
        self.control = ing(80, 'Pearl-control', scoop, D('1.4'), D('50'), D('110'), 75)
        self.cup = ing(67, 'Dabba Cup 12oz', pcs, D('4.2'), D('99'))

        # Menu: Milk Tea category, real size group (U-Cup 16/22), phantom Dabba.
        self.cat = ItemCategory.objects.create(name='Milk Tea')
        ucup = VariantGroup.objects.create(name='U - Cup size')
        self.u16 = VariantOption.objects.create(group=ucup, name='U Cup 16oz')
        self.u22 = VariantOption.objects.create(group=ucup, name='U cup 22oz')
        dabba = VariantGroup.objects.create(name='Dabba cup size')
        self.v12 = VariantOption.objects.create(group=dabba, name='12oz')
        # U-Cup attached to Milk Tea, NOT required (the bug).
        self.cvg = CategoryVariantGroup.objects.create(
            category=self.cat, group=ucup, is_required_override=None)
        # The other two at-risk (size-scoped) categories that must also be forced.
        self.cat_latte = ItemCategory.objects.create(name='Latte')
        self.cat_ft = ItemCategory.objects.create(name='Fruit Tea')
        self.cvg_latte = CategoryVariantGroup.objects.create(
            category=self.cat_latte, group=ucup, is_required_override=None)
        self.cvg_ft = CategoryVariantGroup.objects.create(
            category=self.cat_ft, group=ucup, is_required_override=None)
        # A frappe category sharing the group but NOT to be forced (has base
        # recipes) — negative control: its override must stay None.
        self.cat_frappe = ItemCategory.objects.create(name='Frappe')
        self.cvg_frappe = CategoryVariantGroup.objects.create(
            category=self.cat_frappe, group=ucup, is_required_override=None)

        self.mt = Item.objects.create(
            name='Milk Tea - Classic', price=D('39'), stock=0, category=self.cat)
        # Phantom: recipe line for the Dabba "12oz" option (not offered on MT).
        self.phantom = RecipeIngredient.objects.create(
            item=self.mt, variant=self.v12, ingredient=self.cup, quantity_used=D('1'))
        # Legit U-Cup line — negative control, must survive.
        self.legit = RecipeIngredient.objects.create(
            item=self.mt, variant=self.u16, ingredient=self.control, quantity_used=D('1'))

        # One real sale so the fingerprint guard checks real rows.
        Shift.objects.create(cashier=self.cashier, opening_cash=D('0'), is_open=True)
        self.sale_item = Item.objects.create(name='Brewed Coffee', price=D('50'), stock=100)
        self.txn = create_pos_transaction(
            [{'item_id': self.sale_item.id, 'quantity': 1}],
            'cash', cashier=self.cashier, cash_received=D('100'),
        )

    def _run(self, *args):
        out = StringIO()
        call_command('fix_recipe_prod', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        out = self._run()
        self.nata.refresh_from_db(); self.cvg.refresh_from_db()
        self.assertIn('DRY RUN', out)
        self.assertIsNone(self.nata.purchase_to_base_factor)
        self.assertIsNone(self.nata.last_purchase_price)
        self.assertIsNone(self.nata.purchase_unit_id)
        self.assertTrue(RecipeIngredient.objects.filter(pk=self.phantom.pk).exists())
        for cvg in (self.cvg, self.cvg_latte, self.cvg_ft, self.cvg_frappe):
            cvg.refresh_from_db()
            self.assertIsNone(cvg.is_required_override)

    def test_apply_full_end_state(self):
        out = self._run('--apply')
        self.nata.refresh_from_db(); self.cvg.refresh_from_db()
        self.assertIn('APPLIED', out)
        # 1. Nata config mirrors fruit jelly; cost untouched.
        self.assertEqual(self.nata.purchase_to_base_factor, 108)
        self.assertEqual(self.nata.last_purchase_price, D('280'))
        self.assertEqual(self.nata.purchase_unit_id, self.fj.purchase_unit_id)
        self.assertEqual(self.nata.cost_per_unit, D('2.59'))
        # 2. Phantom 12oz line gone; legit U-Cup line survives.
        self.assertFalse(RecipeIngredient.objects.filter(pk=self.phantom.pk).exists())
        self.assertTrue(RecipeIngredient.objects.filter(pk=self.legit.pk).exists())
        # 3. Size now required on the 3 at-risk categories; frappe left alone.
        self.cvg_latte.refresh_from_db(); self.cvg_ft.refresh_from_db()
        self.cvg_frappe.refresh_from_db()
        self.assertIs(self.cvg.is_required_override, True)
        self.assertIs(self.cvg_latte.is_required_override, True)
        self.assertIs(self.cvg_ft.is_required_override, True)
        self.assertIsNone(self.cvg_frappe.is_required_override)  # not forced
        # Negative controls untouched.
        self.control.refresh_from_db()
        self.assertEqual(self.control.cost_per_unit, D('1.4'))
        self.legit.refresh_from_db()
        self.assertEqual(self.legit.quantity_used, D('1'))
        self.assertEqual(PosTransaction.objects.count(), 1)
        self.assertEqual(PosTransaction.objects.get(pk=self.txn.pk).net_total,
                         self.txn.net_total)

    def test_idempotent_second_apply_all_skips(self):
        self._run('--apply')
        out = self._run('--apply')
        self.assertIn('APPLIED 0 change(s)', out)
        self.nata.refresh_from_db()
        self.assertEqual(self.nata.purchase_to_base_factor, 108)

    def test_guard_aborts_atomically_on_partial_nata_config(self):
        # Someone half-configured Nata since the analysis -> abort, nothing
        # written (phantom line + size override must also be untouched).
        Ingredient.objects.filter(pk=44).update(purchase_to_base_factor=200)
        with self.assertRaises(CommandError):
            self._run('--apply')
        self.assertTrue(RecipeIngredient.objects.filter(pk=self.phantom.pk).exists())
        self.cvg.refresh_from_db()
        self.assertIsNone(self.cvg.is_required_override)

    def test_unexpected_phantom_option_aborts(self):
        # A Dabba-group line at an option we never analysed must abort, not be
        # guessed at and deleted.
        VariantOption.objects.filter(pk=self.v12.pk).update(name='18oz')
        with self.assertRaises(CommandError):
            self._run('--apply')
        self.nata.refresh_from_db()
        self.assertIsNone(self.nata.purchase_to_base_factor)  # atomic: nothing written
        self.assertTrue(RecipeIngredient.objects.filter(pk=self.phantom.pk).exists())

    def test_template_drift_aborts(self):
        # If the fruit jelly template isn't in the expected state, we can't
        # trust the mirror -> abort before touching Nata.
        Ingredient.objects.filter(pk=94).update(purchase_to_base_factor=99)
        with self.assertRaises(CommandError):
            self._run('--apply')
        self.nata.refresh_from_db()
        self.assertIsNone(self.nata.purchase_to_base_factor)
