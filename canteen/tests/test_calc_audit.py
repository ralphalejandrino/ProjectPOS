"""Independent calculation audit — hand-computed expected values for the money
paths the demo data doesn't exercise (VAT-enabled output-VAT extraction, SC/PWD
VAT-exemption, promo, split tender, refund netting). Every number below is worked
out by hand in the comments so a drift in the arithmetic fails exactly one assert.
"""
from decimal import Decimal
from rest_framework.test import APITestCase

from canteen.models import BusinessProfile, User, Item, Shift, PosTransaction
from canteen.services import create_pos_transaction, refund_transaction, close_shift_and_finalize_z


class CalcAuditTests(APITestCase):
    def setUp(self):
        # VAT-enabled, 12% inclusive — the config the demo does NOT use.
        self.bp = BusinessProfile.objects.create(
            business_name='Audit Cafe', currency='PHP',
            vat_enabled=True, vat_rate=Decimal('12'), vat_inclusive=True,
            track_inventory=False, printer_mode='disabled',
            sc_discount_rate=Decimal('20'), pwd_discount_rate=Decimal('20'),
            promo_discount_enabled=True,
        )
        self.cashier = User.objects.create_user(username='c', password='x', role='cashier')
        self.shift = Shift.objects.create(cashier=self.cashier, opening_cash=Decimal('0'), is_open=True)
        # Prices already VAT-inclusive.
        self.a = Item.objects.create(name='A', price=Decimal('112.00'), purchase_price=Decimal('0'), stock=9999)
        self.b = Item.objects.create(name='B', price=Decimal('100.00'), purchase_price=Decimal('0'), stock=9999)

    def test_vat_inclusive_normal_sale(self):
        # 112.00 inclusive → output VAT = 112*12/112 = 12.00 ; vatable = 100.00
        t = create_pos_transaction([{'item_id': self.a.id, 'quantity': 1}], 'cash',
                                   cashier=self.cashier, cash_received=Decimal('200'))
        self.assertEqual(t.net_total, Decimal('112.00'))
        self.assertEqual(t.vatable_sales, Decimal('100.00'))          # 112 - 12
        self.assertEqual(t.vat_exempt_amount, Decimal('0.00'))
        self.assertEqual(t.change_given, Decimal('88.00'))            # 200 - 112

    def test_sc_discount_vat_exempt(self):
        # 112 inclusive → VAT-exclusive 100.00 ; VAT removed 12.00 ; SC 20% of 100 = 20.00
        # net = 112 - 12 - 20 = 80.00 ; entire net is VAT-exempt.
        t = create_pos_transaction([{'item_id': self.a.id, 'quantity': 1}], 'cash',
                                   cashier=self.cashier, discount_amount=Decimal('20.00'),
                                   discount_type='sc', discount_id_number='SC-1',
                                   cash_received=Decimal('80'))
        self.assertTrue(t.vat_exempt)
        self.assertEqual(t.vat_amount, Decimal('12.00'))             # VAT removed
        self.assertEqual(t.discount_total, Decimal('20.00'))
        self.assertEqual(t.net_total, Decimal('80.00'))
        self.assertEqual(t.vat_exempt_amount, Decimal('80.00'))
        self.assertEqual(t.vatable_sales, Decimal('0.00'))

    def test_promo_half_off(self):
        # 100 inclusive → promo 50% = 50.00 ; net = 50.00 ; output VAT on net = 50*12/112 = 5.36
        t = create_pos_transaction([{'item_id': self.b.id, 'quantity': 1}], 'cash',
                                   cashier=self.cashier, discount_amount=Decimal('50.00'),
                                   discount_type='promo', cash_received=Decimal('50'))
        self.assertEqual(t.discount_total, Decimal('50.00'))
        self.assertEqual(t.net_total, Decimal('50.00'))
        self.assertEqual(t.vatable_sales, Decimal('44.64'))          # 50 - round(50*12/112=5.357=5.36)

    def test_split_tender_sums_to_net(self):
        # 112 net paid 50 cash + 62 gcash
        t = create_pos_transaction([{'item_id': self.a.id, 'quantity': 1}], 'cash',
                                   cashier=self.cashier,
                                   payment_lines=[{'method': 'cash', 'amount': '50.00'},
                                                  {'method': 'gcash', 'amount': '62.00'}])
        lines = {l.method: l.amount for l in t.payment_lines.all()}
        self.assertEqual(sum(lines.values()), Decimal('112.00'))
        self.assertEqual(lines['cash'], Decimal('50.00'))
        self.assertEqual(lines['gcash'], Decimal('62.00'))

    def test_refund_mirrors_negatives(self):
        t = create_pos_transaction([{'item_id': self.a.id, 'quantity': 1}], 'cash',
                                   cashier=self.cashier, cash_received=Decimal('112'))
        r = refund_transaction(t.id, performed_by=self.cashier)
        self.assertEqual(r.transaction_type, 'refund')
        self.assertEqual(Decimal(str(r.net_total)), Decimal('-112.00'))
        self.assertEqual(Decimal(str(r.vatable_sales)), Decimal('-100.00'))
        self.assertEqual(r.refund_of_id, t.id)

    def test_z_report_refund_accounting(self):
        # Sale 112 (cash) then refund it (cash out). BIR-correct Z treatment:
        #  - gross/net SALES stay PURE sales (112) — refunds are NOT deducted from
        #    the sales lines; they surface on a dedicated Refunds deduction line;
        #  - the refunded cash nets in the DRAWER reconciliation, not in sales;
        #  - the accumulated grand total tracks gross sales (refunds excluded).
        t = create_pos_transaction([{'item_id': self.a.id, 'quantity': 1}], 'cash',
                                   cashier=self.cashier, cash_received=Decimal('112'))
        refund_transaction(t.id, performed_by=self.cashier)
        z = close_shift_and_finalize_z(self.shift.id, Decimal('0'), self.cashier)
        self.assertEqual(Decimal(str(z.gross_sales)), Decimal('112.00'))   # pure sales
        self.assertEqual(Decimal(str(z.net_sales)), Decimal('112.00'))     # pure sales
        self.assertEqual(Decimal(str(z.refund_total)), Decimal('112.00'))  # deduction line
        self.assertEqual(z.refund_count, 1)
        # Drawer: opening 0 + cash collected 112 - refund cash 112 = 0 expected;
        # counted 0 → no over/short. This is where the refund actually nets.
        self.assertEqual(Decimal(str(z.cash_expected)), Decimal('0.00'))
        self.assertEqual(Decimal(str(z.over_short)), Decimal('0.00'))
