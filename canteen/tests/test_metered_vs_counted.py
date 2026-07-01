"""FEATURE-053 — metered vs counted ingredients.

Metered ingredients (milk, beans) are auto-deducted per recipe on every sale.
Counted ingredients (tea bags, garnishes) are tracked by physical count only —
never auto-deducted — because per-sale precision can't be measured. Either way
their per-serving cost still feeds the recipe cost (FEATURE-052). The
distinction is Ingredient.track_depletion, editable via the API.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from canteen.models import (
    BusinessProfile, User, IngredientUnit, Ingredient, Item, RecipeIngredient,
    Shift,
)
from canteen.services import create_pos_transaction
from canteen.serializers import ItemSerializer


class MeteredVsCountedTests(APITestCase):
    def setUp(self):
        self.bp = BusinessProfile.objects.create(
            business_name='Demo Cafe', currency='PHP',
            vat_enabled=False, track_inventory=True, printer_mode='disabled',
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )
        self.manager = User.objects.create_user(
            username='manager', password='x', role='manager'
        )
        self.shift = Shift.objects.create(
            cashier=self.cashier, opening_cash=Decimal('0'), is_open=True
        )
        self.g, _ = IngredientUnit.objects.get_or_create(
            abbreviation='g', defaults={'name': 'Gram'}
        )
        self.pcs, _ = IngredientUnit.objects.get_or_create(
            abbreviation='pcs', defaults={'name': 'Pieces'}
        )
        self.beans = Ingredient.objects.create(  # metered
            name='Beans', unit=self.g, cost_per_unit=Decimal('0.5000'),
            current_stock=Decimal('1000'), track_depletion=True,
        )
        self.teabag = Ingredient.objects.create(  # counted
            name='Tea Bag', unit=self.pcs, cost_per_unit=Decimal('2.0000'),
            current_stock=Decimal('50'), track_depletion=False,
        )
        self.tea = Item.objects.create(
            name='Brewed Tea', price=Decimal('95.00'), stock=100,
        )
        RecipeIngredient.objects.create(
            item=self.tea, ingredient=self.teabag, quantity_used=Decimal('1'),
        )
        RecipeIngredient.objects.create(
            item=self.tea, ingredient=self.beans, quantity_used=Decimal('5'),
        )

    def _sell(self, qty=1):
        return create_pos_transaction(
            [{'item_id': self.tea.id, 'quantity': qty}], 'cash',
            cashier=self.cashier, cash_received=Decimal('500'),
        )

    def test_counted_not_depleted_but_metered_is(self):
        self._sell(1)
        self.teabag.refresh_from_db()
        self.beans.refresh_from_db()
        # counted tea bag: physical count untouched by the sale
        self.assertEqual(self.teabag.current_stock, Decimal('50.0000'))
        # metered beans: 5 g deducted
        self.assertEqual(self.beans.current_stock, Decimal('995.0000'))

    def test_counted_still_contributes_per_serving_cost(self):
        data = ItemSerializer(self.tea).data
        # 1 bag*2.00 + 5 g*0.50 = 2.00 + 2.50 = 4.50
        self.assertEqual(Decimal(data['recipe_cost']), Decimal('4.5000'))

    def test_track_depletion_roundtrips_via_api(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.patch(
            f'/api/canteen/ingredients/{self.beans.id}/',
            {'track_depletion': False}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.beans.refresh_from_db()
        self.assertFalse(self.beans.track_depletion)
        # ...and once counted, it is no longer depleted on sale.
        self._sell(1)
        self.beans.refresh_from_db()
        self.assertEqual(self.beans.current_stock, Decimal('1000.0000'))
