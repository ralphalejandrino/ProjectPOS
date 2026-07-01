"""PROD v1 end-to-end calculation reconciliation (FEATURE-050–055).

One realistic scenario exercised through the whole money/inventory pipeline with
every expected value computed by hand, so the arithmetic is pinned end to end:

  purchase (packages → base units) → weighted-average cost → recipe-derived cost
  → sale (metered depletion, counted NOT depleted, cost-at-sale snapshot) →
  weekly COGS / gross profit / margin → count & reconcile (variance = waste).

If any calculation drifts, exactly one assertion here fails and names it.
"""

from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, User, IngredientUnit, Ingredient, Item, RecipeIngredient,
    Shift, IngredientRestockLog,
)
from canteen.serializers import ItemSerializer, IngredientRestockLogSerializer
from canteen.services import create_pos_transaction
from canteen.views import _weekly_cogs, _weekly_payload


class V1ReconciliationTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP',
            # track_inventory ON is the realistic PROD config — and REQUIRED:
            # ingredient depletion (and thus accurate COGS / "Can make now")
            # only runs when this global flag is set (services.py:797).
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(username='cashier', password='x', role='cashier')
        self.shift = Shift.objects.create(cashier=self.cashier, opening_cash=Decimal('0'), is_open=True)
        self.g, _ = IngredientUnit.objects.get_or_create(abbreviation='g', defaults={'name': 'Gram'})
        self.ml, _ = IngredientUnit.objects.get_or_create(abbreviation='ml', defaults={'name': 'Millilitre'})
        self.pcs, _ = IngredientUnit.objects.get_or_create(abbreviation='pcs', defaults={'name': 'Pieces'})
        self.sack, _ = IngredientUnit.objects.get_or_create(abbreviation='sack', defaults={'name': 'Sack'})

        # Sugar: bought by the sack (25,000 g/sack), starts empty.
        self.sugar = Ingredient.objects.create(
            name='Sugar', unit=self.g, cost_per_unit=Decimal('0'), current_stock=Decimal('0'),
            purchase_unit=self.sack, purchase_to_base_factor=Decimal('25000'),
        )
        # Milk: metered, per-ml cost.
        self.milk = Ingredient.objects.create(
            name='Milk', unit=self.ml, cost_per_unit=Decimal('0.1000'), current_stock=Decimal('10000'),
        )
        # Tea bag: COUNTED (not depleted per sale), per-piece cost.
        self.teabag = Ingredient.objects.create(
            name='Tea Bag', unit=self.pcs, cost_per_unit=Decimal('5.0000'),
            current_stock=Decimal('100'), track_depletion=False,
        )

    def _restock_pkg(self, ing, packages, price):
        s = IngredientRestockLogSerializer(
            data={'packages': str(packages), 'package_price': str(price)},
            context={'ingredient': ing},
        )
        assert s.is_valid(), s.errors
        s.save(ingredient=ing)

    def test_full_pipeline_reconciles(self):
        # ── 1. Package restock → base-unit conversion + weighted-average cost ──
        # 1 sack @ 1250 → 25,000 g @ 0.05/g; prior stock 0 → adopt new price.
        self._restock_pkg(self.sugar, 1, 1250)
        self.sugar.refresh_from_db()
        self.assertEqual(self.sugar.current_stock, Decimal('25000.0000'))
        self.assertEqual(self.sugar.cost_per_unit, Decimal('0.0500'))
        # 1 sack @ 1500 → 25,000 g @ 0.06/g; weighted avg:
        # (25000*0.05 + 25000*0.06) / 50000 = 2750/50000 = 0.055
        self._restock_pkg(self.sugar, 1, 1500)
        self.sugar.refresh_from_db()
        self.assertEqual(self.sugar.current_stock, Decimal('50000.0000'))
        self.assertEqual(self.sugar.cost_per_unit, Decimal('0.0550'))

        # ── 2. Recipe-derived cost (metered + counted ingredients) ──
        tea = Item.objects.create(name='Sweet Milk Tea', price=Decimal('100.00'),
                                  purchase_price=Decimal('0'), stock=1000)
        RecipeIngredient.objects.create(item=tea, ingredient=self.sugar, quantity_used=Decimal('10'))
        RecipeIngredient.objects.create(item=tea, ingredient=self.milk, quantity_used=Decimal('200'))
        RecipeIngredient.objects.create(item=tea, ingredient=self.teabag, quantity_used=Decimal('1'))
        # 10*0.055 + 200*0.10 + 1*5.00 = 0.55 + 20.00 + 5.00 = 25.55
        data = ItemSerializer(tea).data
        self.assertEqual(Decimal(data['recipe_cost']), Decimal('25.5500'))
        # margin off price 100: (100-25.55)/100*100 = 74.45. The peso margin keeps
        # 2 decimals; the per-item margin badge is intentionally 1-decimal (74.5).
        self.assertEqual(Decimal(data['effective_margin']), Decimal('74.45'))
        self.assertEqual(data['effective_margin_pct'], 74.5)

        # ── 3. Sell 3 → depletion (metered yes, counted no) + cost snapshot ──
        for _ in range(3):
            txn = create_pos_transaction(
                [{'item_id': tea.id, 'quantity': 1}], 'cash',
                cashier=self.cashier, cash_received=Decimal('500'),
            )
        self.sugar.refresh_from_db(); self.milk.refresh_from_db(); self.teabag.refresh_from_db()
        self.assertEqual(self.sugar.current_stock, Decimal('49970.0000'))   # 50000 - 3*10
        self.assertEqual(self.milk.current_stock, Decimal('9400.0000'))     # 10000 - 3*200
        self.assertEqual(self.teabag.current_stock, Decimal('100.0000'))    # counted → untouched
        # cost-at-sale snapshot frozen at the recipe cost
        self.assertEqual(txn.items.first().unit_cost, Decimal('25.5500'))

        # ── 4. Weekly COGS / gross profit / margin ──
        today = timezone.localdate()
        cogs, costed, total = _weekly_cogs(today, today)
        self.assertEqual(cogs, Decimal('76.65'))          # 3 * 25.55
        self.assertEqual(costed, total)                    # every line costed
        payload, err = _weekly_payload(today.strftime('%Y-%m-%d'))
        self.assertIsNone(err)
        prof = payload['profitability']
        self.assertEqual(Decimal(prof['cogs']), Decimal('76.65'))
        self.assertEqual(Decimal(prof['gross_profit']), Decimal('223.35'))  # 300 - 76.65
        self.assertAlmostEqual(prof['gross_margin_pct'], 74.45, places=2)   # 223.35/300*100

        # ── 5. Count & reconcile → variance = waste ──
        self.client.force_authenticate(
            User.objects.create_user(username='mgr', password='x', role='manager')
        )
        resp = self.client.post(
            '/api/canteen/ingredients/reconcile/',
            {'counts': [{'id': self.sugar.id, 'counted_stock': '49900'}]}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.sugar.refresh_from_db()
        self.assertEqual(self.sugar.current_stock, Decimal('49900.0000'))
        self.assertEqual(resp.data['results'][0]['variance'], -70.0)  # 49900 - 49970
