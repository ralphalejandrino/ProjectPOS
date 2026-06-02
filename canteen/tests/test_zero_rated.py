"""FEATURE-034 — Item.zero_rated VAT routing.

Zero-rated (VAT-exempt) items book their full sale amount to
PosTransaction.zero_rated_sales and carry no output VAT. The VAT-able
base (vatable_sales / output_vat) must exclude the zero-rated portion,
and the BIR accounting identity must close.

VAT config mirrors the standard PH setup: 12% VAT-inclusive.
"""

from decimal import Decimal

from django.utils import timezone as dj_tz
from rest_framework.test import APITestCase

from canteen.models import BusinessProfile, Item, Shift, User
from canteen.services import close_shift_and_finalize_z, create_pos_transaction


def _bp():
    bp = BusinessProfile.get_instance()
    bp.vat_enabled = True
    bp.vat_inclusive = True
    bp.vat_rate = Decimal('12.00')
    bp.track_inventory = True
    bp.printer_mode = 'disabled'
    bp.machine_identification_number = 'MIN-001'
    bp.business_name = 'Test Canteen'
    bp.tin = '123-456-789'
    bp.address = 'Manila'
    bp.save()
    return bp


class ZeroRatedRoutingTests(APITestCase):
    def setUp(self):
        self.bp = _bp()
        self.user = User.objects.create_user(
            username='cash1', password='x', role='cashier'
        )
        # VAT-able item priced so VAT extraction is exact: 112 → vat 12, net 100.
        self.vatable = Item.objects.create(
            name='Coffee', price=Decimal('112.00'), stock=10000
        )
        # Zero-rated (unprocessed food): no output VAT on its sale amount.
        self.zero = Item.objects.create(
            name='Rice', price=Decimal('50.00'), stock=10000, zero_rated=True
        )

    def _open_shift(self):
        return Shift.objects.create(
            cashier=self.user, opening_cash=Decimal('100.00'), is_open=True
        )

    def _sell(self, cart):
        return create_pos_transaction(cart, 'cash', cashier=self.user)

    def test_zero_rated_item_books_zero_rated_sales_no_vat(self):
        """Pure zero-rated sale → zero_rated_sales set, no VAT-able sales."""
        self._open_shift()
        t = self._sell([{'item_id': self.zero.id, 'quantity': 1}])
        self.assertEqual(t.zero_rated_sales, Decimal('50.00'))
        self.assertEqual(t.vatable_sales, Decimal('0.00'))
        self.assertEqual(t.vat_amount, Decimal('0.00'))
        self.assertEqual(t.gross_total, Decimal('50.00'))
        self.assertEqual(t.net_total, Decimal('50.00'))

    def test_mixed_cart_accounting_closes(self):
        """Mixed cart: VAT-able (112) + zero-rated (50) in one transaction.

        gross = (net - zero_rated) + vat_amount + zero_rated must close, and
        VAT-able sales/output VAT exclude the zero-rated line.
        """
        self._open_shift()
        t = self._sell([
            {'item_id': self.vatable.id, 'quantity': 1},
            {'item_id': self.zero.id, 'quantity': 1},
        ])
        self.assertEqual(t.gross_total, Decimal('162.00'))
        self.assertEqual(t.net_total, Decimal('162.00'))
        self.assertEqual(t.zero_rated_sales, Decimal('50.00'))
        # VAT-able portion only: 112 inclusive → net 100, output VAT 12.
        self.assertEqual(t.vatable_sales, Decimal('100.00'))
        self.assertEqual(t.vat_amount, Decimal('0.00'))

        # Accounting closes (spec identity).
        closes = (
            (t.net_total - t.zero_rated_sales) + t.vat_amount + t.zero_rated_sales
        )
        self.assertEqual(closes, t.gross_total)

        # System BIR identity: net (VAT-inclusive) = vatable + output_vat +
        # vat_exempt + zero_rated. Output VAT for this txn is 12.
        output_vat = t.net_total - t.vatable_sales - t.zero_rated_sales
        self.assertEqual(output_vat, Decimal('12.00'))

    def test_zreport_snapshot_includes_zero_rated_total(self):
        """Z snapshot sums zero_rated_sales and excludes it from output VAT."""
        self._open_shift()
        self._sell([{'item_id': self.zero.id, 'quantity': 1}])     # zr 50
        self._sell([{'item_id': self.zero.id, 'quantity': 1}])     # zr 50
        self._sell([{'item_id': self.vatable.id, 'quantity': 1}])  # vatable 112

        shift = Shift.objects.get(cashier=self.user, is_open=True)
        z = close_shift_and_finalize_z(shift.id, Decimal('300.00'), self.user)

        self.assertEqual(z.zero_rated_sales, Decimal('100.00'))
        self.assertEqual(z.vatable_sales, Decimal('100.00'))
        # Output VAT excludes both zero-rated lines (only the 112 line is taxed).
        self.assertEqual(z.output_vat, Decimal('12.00'))
        self.assertEqual(z.gross_sales, Decimal('212.00'))
