"""#8 shift-span countermeasure — a sale is blocked on a shift opened on a
PRIOR PHT day, forcing the cashier to close yesterday's Z (with a cash count)
and open a fresh shift. Shifts spanning midnight are the source of the mis-dated
Z's (a Z is dated by its shift's OPEN day)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from pos.models import BusinessProfile, Item, Shift, User
from pos.services import create_pos_transaction


class ShiftSpanGuardTests(TestCase):
    def setUp(self):
        BusinessProfile.objects.create(
            business_name='PROD', currency='PHP', vat_enabled=False,
            track_inventory=False, printer_mode='disabled')
        self.cashier = User.objects.create_user(
            username='c', password='x', role='cashier')
        self.item = Item.objects.create(
            name='Latte', price=Decimal('59.00'), stock=0)

    def _sell(self):
        return create_pos_transaction(
            [{'item_id': self.item.id, 'quantity': 1}], 'cash',
            cashier=self.cashier, cash_received=Decimal('100.00'))

    def test_sale_ok_on_same_day_shift(self):
        Shift.objects.create(cashier=self.cashier,
                             opening_cash=Decimal('0.00'), is_open=True)
        self.assertIsNotNone(self._sell())

    def test_sale_blocked_on_prior_day_shift(self):
        s = Shift.objects.create(cashier=self.cashier,
                                 opening_cash=Decimal('0.00'), is_open=True)
        # Relocate the shift's OPEN time to yesterday (bypasses auto_now_add).
        Shift.objects.filter(pk=s.pk).update(
            opened_at=timezone.now() - timedelta(days=1))
        with self.assertRaises(DRFValidationError):
            self._sell()
