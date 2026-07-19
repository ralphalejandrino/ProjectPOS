"""Tests for the one-time fix_ingredient_costing command (PROD costing
correction, manager-confirmed values 2026-07-19). The fixture recreates the
exact live-box state the command's guards expect (forced pks); tests pin the
full end state, dry-run inertness, idempotency, atomic guard aborts, and that
sales records + unrelated ingredients are untouched."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from canteen.models import (
    BusinessProfile, Ingredient, IngredientRestockLog, IngredientUnit, Item,
    PosTransaction, RecipeIngredient, Shift, User, VariantGroup, VariantOption,
)
from canteen.services import create_pos_transaction

D = Decimal


class FixIngredientCostingTests(TestCase):
    def setUp(self):
        BusinessProfile.objects.create(
            business_name='PROD', currency='PHP', vat_enabled=False,
            track_inventory=True, printer_mode='disabled',
        )
        self.admin = User.objects.create_user(username='admin', password='x', role='admin')
        self.cashier = User.objects.create_user(username='cashier', password='x', role='cashier')
        ml = IngredientUnit.objects.get_or_create(abbreviation='ml', defaults={'name': 'milliliter'})[0]
        g = IngredientUnit.objects.get_or_create(abbreviation='g', defaults={'name': 'gram'})[0]
        pcs = IngredientUnit.objects.get_or_create(abbreviation='pcs', defaults={'name': 'pieces'})[0]
        scoop = IngredientUnit.objects.get_or_create(abbreviation='scoop', defaults={'name': 'scoop'})[0]
        self.g_unit = g

        def ing(pk, name, unit, cost, stock, price=None, factor=None, p_unit=None):
            i = Ingredient.objects.create(
                id=pk, name=name, unit=unit, cost_per_unit=D('0'),
                current_stock=D('0'), purchase_unit=p_unit,
            )
            # Set live-box state via update() so Ingredient/RestockLog save()
            # side effects can't drift the fixture.
            Ingredient.objects.filter(pk=pk).update(
                cost_per_unit=cost, current_stock=stock,
                last_purchase_price=price, purchase_to_base_factor=factor,
            )
            i.refresh_from_db()
            return i

        self.milk = ing(22, 'Fullcream milk', ml, D('1.0068'), D('144'), D('76.5'), D('1000'))
        self.choco = ing(34, 'Choco mousse', scoop, D('0.8063'), D('0'), D('400'), D('100'))
        self.espresso = ing(19, 'Espresso Syrup', ml, D('2.74'), D('500'), D('350'), D('960'))
        self.dabba = ing(67, 'Dabba Cup 12oz', pcs, D('0'), D('99'), D('420'), D('100'), pcs)
        self.condensed = ing(51, 'Condensed', ml, D('0'), D('75'), D('50'), D('200'), g)
        self.wcs = ing(54, 'Waffle choco syrup', ml, D('0'), D('-10'))
        self.caramel = ing(55, 'waffle caramel syrup', ml, D('0.72'), D('100'), D('280'), D('385'), g)
        self.blacktea = ing(49, 'Blacktea', pcs, D('24'), D('20'), D('240'), D('10'), g)
        self.control = ing(80, 'Pearl-control', scoop, D('1.4'), D('50'), D('110'), D('75'))

        # Restock rows as on the box. bulk_create skips the FEATURE-051 roll.
        IngredientRestockLog.objects.bulk_create([
            IngredientRestockLog(id=6, ingredient=self.milk,
                                 quantity_added=D('144'), cost_per_unit=D('76.5833')),
            IngredientRestockLog(id=38, ingredient=self.choco,
                                 quantity_added=D('730'), cost_per_unit=D('0.78')),
        ])

        grp = VariantGroup.objects.create(name='Size')
        self.v12 = VariantOption.objects.create(group=grp, name='12oz')
        self.v16 = VariantOption.objects.create(group=grp, name='16oz')
        self.melon = Item.objects.create(name='Latte - Melon', price=D('69'), stock=0)
        self.rv = Item.objects.create(name='Milk Tea - Red Velvet', price=D('59'), stock=0)
        RecipeIngredient.objects.create(item=self.melon, variant=self.v12,
                                        ingredient=self.blacktea, quantity_used=D('100'))
        RecipeIngredient.objects.create(item=self.melon, variant=self.v16,
                                        ingredient=self.blacktea, quantity_used=D('150'))
        self.rv_line = RecipeIngredient.objects.create(
            item=self.rv, variant=self.v12, ingredient=self.blacktea, quantity_used=D('200'))
        self.control_line = RecipeIngredient.objects.create(
            item=self.rv, variant=self.v12, ingredient=self.control, quantity_used=D('1'))

        # One real sale so the sales fingerprint guard checks real rows.
        Shift.objects.create(cashier=self.cashier, opening_cash=D('0'), is_open=True)
        self.sale_item = Item.objects.create(name='Brewed Coffee', price=D('50.00'), stock=100)
        self.txn = create_pos_transaction(
            [{'item_id': self.sale_item.id, 'quantity': 1}],
            'cash', cashier=self.cashier, cash_received=D('100.00'),
        )

    def _run(self, *args):
        out = StringIO()
        call_command('fix_ingredient_costing', *args, stdout=out)
        return out.getvalue()

    def _refresh_all(self):
        for i in (self.milk, self.choco, self.espresso, self.dabba,
                  self.condensed, self.wcs, self.control):
            i.refresh_from_db()

    def test_dry_run_writes_nothing(self):
        out = self._run()
        self._refresh_all()
        self.assertIn('DRY RUN', out)
        self.assertEqual(self.milk.cost_per_unit, D('1.0068'))
        self.assertEqual(self.milk.current_stock, D('144'))
        r6 = IngredientRestockLog.objects.get(pk=6)
        self.assertEqual(r6.quantity_added, D('144'))
        self.assertIsNone(r6.corrected_at)
        self.assertFalse(IngredientRestockLog.objects.get(pk=38).is_voided)
        self.assertEqual(self.espresso.cost_per_unit, D('2.74'))
        self.assertEqual(
            RecipeIngredient.objects.filter(item=self.melon, ingredient=self.blacktea).count(), 2)

    def test_apply_full_end_state(self):
        out = self._run('--apply')
        self._refresh_all()
        self.assertIn('APPLIED', out)
        # Milk: entry corrected, cost recomputed, package memory restored.
        r6 = IngredientRestockLog.objects.get(pk=6)
        self.assertEqual(r6.quantity_added, D('12000'))
        self.assertEqual(r6.cost_per_unit, D('0.0766'))
        self.assertEqual(r6.corrected_by, self.admin)
        self.assertEqual(self.milk.cost_per_unit, D('0.0766'))
        self.assertEqual(self.milk.current_stock, D('12000'))  # 144 + 11856
        self.assertEqual(self.milk.last_purchase_price, D('76.58'))
        # Choco: phantom voided, stock removed, direct cost landed.
        r38 = IngredientRestockLog.objects.get(pk=38)
        self.assertTrue(r38.is_voided)
        self.assertEqual(r38.voided_by, self.admin)
        self.assertEqual(self.choco.current_stock, D('-730'))
        self.assertEqual(self.choco.cost_per_unit, D('4.0000'))
        # Direct sets.
        self.assertEqual(self.espresso.cost_per_unit, D('0.3646'))
        self.assertEqual(self.dabba.cost_per_unit, D('4.2000'))
        self.assertEqual(self.condensed.cost_per_unit, D('0.1467'))
        self.assertEqual(self.condensed.last_purchase_price, D('44'))
        self.assertEqual(self.condensed.purchase_to_base_factor, D('300'))
        self.assertEqual(self.wcs.cost_per_unit, D('0.7273'))
        self.assertEqual(self.wcs.last_purchase_price, D('280'))
        self.assertEqual(self.wcs.purchase_to_base_factor, D('385'))
        self.assertEqual(self.wcs.purchase_unit_id, self.caramel.purchase_unit_id)
        # Blacktea recipe lines.
        self.assertEqual(
            RecipeIngredient.objects.filter(item=self.melon, ingredient=self.blacktea).count(), 0)
        self.rv_line.refresh_from_db()
        self.assertEqual(self.rv_line.quantity_used, D('0.05'))
        # Negative controls: unrelated ingredient/line + sales untouched.
        self.assertEqual(self.control.cost_per_unit, D('1.4'))
        self.control_line.refresh_from_db()
        self.assertEqual(self.control_line.quantity_used, D('1'))
        txn = PosTransaction.objects.get(pk=self.txn.pk)
        self.assertEqual(txn.net_total, self.txn.net_total)
        self.assertEqual(PosTransaction.objects.count(), 1)

    def test_idempotent_second_apply_all_skips(self):
        self._run('--apply')
        out = self._run('--apply')
        self.assertIn('APPLIED 0 change(s)', out)
        self._refresh_all()
        self.assertEqual(self.milk.cost_per_unit, D('0.0766'))
        self.assertEqual(self.choco.cost_per_unit, D('4.0000'))
        self.assertEqual(self.milk.current_stock, D('12000'))  # no double stock delta

    def test_guard_aborts_atomically_on_unexpected_state(self):
        # Someone changed espresso cost since the audit -> whole run must abort
        # with NOTHING written, including the earlier milk/void steps.
        Ingredient.objects.filter(pk=19).update(cost_per_unit=D('2.00'))
        with self.assertRaises(CommandError):
            self._run('--apply')
        self._refresh_all()
        self.assertEqual(self.milk.cost_per_unit, D('1.0068'))
        self.assertEqual(self.milk.current_stock, D('144'))
        self.assertFalse(IngredientRestockLog.objects.get(pk=38).is_voided)
        self.assertEqual(
            RecipeIngredient.objects.filter(item=self.melon, ingredient=self.blacktea).count(), 2)

    def test_missing_actor_fails_loud(self):
        with self.assertRaises(CommandError):
            self._run('--as-user', 'nonexistent')
