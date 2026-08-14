"""FEATURE-063 — units of measurement on the STOCK MOVEMENT block.

The owner's own request, verbatim: "Saka kailangan may units of measurement
bawat items na nakalagay sa resibo. Nakakalito kung ano yung mga numero. Dapat
merong ml, grams or scoops, etc."

On the Z slip he photographed, the block read "Caramel syrup  50" — 50 of what
is unanswerable, and he is the person who has to act on the number.
"""

from decimal import Decimal

from django.test import TestCase

from pos.models import (
    BusinessProfile, Ingredient, IngredientLog, IngredientUnit, Item,
    PosTransaction, Shift, User,
)
from pos.services import stock_movements_for_shift


class StockMovementUnitTests(TestCase):
    def setUp(self):
        bp = BusinessProfile.get_instance()
        bp.printer_mode = 'usb'
        bp.save()
        self.user = User.objects.create_user(
            username='c1', password='x', role='cashier'
        )
        # ml/g are seeded by a data migration, so get_or_create — creating
        # them outright trips the unique abbreviation constraint.
        self.ml = IngredientUnit.objects.get_or_create(
            abbreviation='ml', defaults={'name': 'Millilitre'})[0]
        self.g = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'})[0]
        self.syrup = Ingredient.objects.create(
            name='Caramel syrup', unit=self.ml,
            cost_per_unit=Decimal('0.5000'), current_stock=Decimal('1000'),
        )
        self.powder = Ingredient.objects.create(
            name='Matcha powder', unit=self.g,
            cost_per_unit=Decimal('2.0000'), current_stock=Decimal('500'),
        )
        self.item = Item.objects.create(
            name='Latte', price=Decimal('69.00'), stock=100
        )
        self.shift = Shift.objects.create(
            cashier=self.user, opening_cash=Decimal('2000.00'), is_open=True
        )
        self.txn = PosTransaction.objects.create(
            cashier=self.user, shift=self.shift, payment_method='cash',
            total_amount=Decimal('69.00'), gross_total=Decimal('69.00'),
            net_total=Decimal('69.00'),
        )

    def _log(self, ingredient, qty):
        # stock_before/after are NOT NULL — the ledger is a snapshot, not a
        # delta, so both ends are recorded at write time.
        before = ingredient.current_stock
        IngredientLog.objects.create(
            ingredient=ingredient, transaction=self.txn, action='sale',
            quantity_change=Decimal(str(-qty)),
            stock_before=before, stock_after=before - Decimal(str(qty)),
        )

    def test_movement_carries_the_ingredients_unit(self):
        self._log(self.syrup, 50)

        mv = stock_movements_for_shift(self.shift)

        self.assertEqual(len(mv), 1)
        self.assertEqual(mv[0]['ingredient_name'], 'Caramel syrup')
        self.assertEqual(mv[0]['unit'], 'ml')
        self.assertEqual(mv[0]['sold'], 50.0)

    def test_each_ingredient_carries_its_OWN_unit(self):
        """Negative control against a hardcoded or first-row unit: two
        ingredients with different units must not both report the same one."""
        self._log(self.syrup, 50)
        self._log(self.powder, 12)

        by_name = {m['ingredient_name']: m for m in stock_movements_for_shift(self.shift)}

        self.assertEqual(by_name['Caramel syrup']['unit'], 'ml')
        self.assertEqual(by_name['Matcha powder']['unit'], 'g')

    def _print_z(self):
        """Render the real thermal Z through the real code path.

        Reimplementing the formatting in the test would only prove the copy
        matches itself — the point is what actually reaches the paper.
        """
        from unittest import mock
        from pos import receipt_service
        from pos.receipt_service import print_z_report
        from pos.services import close_shift_and_finalize_z

        class _FakePrinter:
            def __init__(self):
                self.output = ''
            def text(self, s):
                self.output += s
            def set(self, **kw):
                pass
            def _raw(self, *a, **k):
                pass
            def close(self):
                pass

        z = close_shift_and_finalize_z(
            self.shift.id, Decimal('2000.00'), self.user
        )
        fake = _FakePrinter()
        with mock.patch.object(receipt_service, 'File', return_value=fake):
            print_z_report(z)
        return fake.output

    def test_printed_receipt_shows_the_unit(self):
        """The end the owner actually reads: '50 ml', not a bare '50'."""
        self._log(self.syrup, 50)

        out = self._print_z()

        self.assertIn('STOCK MOVEMENT', out)
        self.assertIn('50 ml', out)

    def test_NEGATIVE_CONTROL_unitless_ingredient_still_prints(self):
        """An ingredient whose unit abbreviation is blank must still render a
        line — a receipt that raises is far worse than one missing a unit."""
        blank = IngredientUnit.objects.get_or_create(
            abbreviation='', defaults={'name': 'Unspecified'})[0]
        odd = Ingredient.objects.create(
            name='Mystery powder', unit=blank,
            cost_per_unit=Decimal('1.0000'), current_stock=Decimal('10'),
        )
        self._log(odd, 3)

        out = self._print_z()

        self.assertIn('Mystery powder', out)
        self.assertIn('3', out)
