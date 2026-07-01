"""seed_demo_v2 — complete, report-populating demo seed for TarsierPOS.

Purpose
    Stand up a demo instance where EVERY current surface has believable data to
    visually verify against: a full POS grid, an ingredients/stock screen with
    live-depleting + makeable states, a low-stock dashboard, non-trivial X/Z
    reports, and populated weekly + period reports (with WoW deltas).

Design decisions (verified against HEAD — Django 5.2.14, migration 0043)
  * SALES GO THROUGH THE REAL PATH. Every sale is rung via
    services.create_pos_transaction (FLAG-047 spirit): it enforces the open
    shift, freezes totals, resolves/validates variants, deducts Item.stock,
    depletes ingredients, writes the IngredientLog ledger, generates the OR
    number, and records PaymentLines. Nothing is hand-forged.

  * is_seed = FALSE (deliberate — session decision 2026-07-01). Every reporting
    surface in this codebase filters is_seed=True OUT (X/Z: views.py:565/2017 +
    services.py:923-929; dashboard: views.py:2528-2578; weekly: 2084-2136;
    period: 2440; insights: 2059-2084). Tagging demo rows is_seed=True would
    render all of those EMPTY — the exact opposite of this command's job. The
    demo's safety comes instead from isolation: this is a dedicated dev/demo DB,
    it is NEVER deployed to a client box, and it is gated behind --confirm.

  * MULTI-DAY VIA BACKDATING. create_pos_transaction stamps created_at=now and
    Shift.opened_at=now. To span history we open a shift per (day, cashier),
    ring that day's sales, finalize the Z, then rewrite created_at (sales +
    their IngredientLog rows), Shift.opened_at/closed_at, and ZReport.
    finalized_at to the historical instant. The weekly/period headline reads
    finalized ZReports by business_date (= localdate of shift.opened_at), while
    the weekly sub-panels (top items, cashiers, busiest hour) read
    PosTransaction.created_at — so BOTH are backdated.

  * TODAY IS LEFT LIVE. The current PHT day gets an OPEN shift with a few sales
    and is NOT finalized, so the live X-report, the dashboard "today" figures,
    and the weekly report's live-day column all populate.

Idempotency
    Phase 1 (static catalog: profile, units, ingredients, categories, variant
    groups, items, recipes, users) uses get_or_create and is safely re-runnable.
    Phase 2 (restock + sales + Z finalization, which are not get_or_create-able
    and would double-count stock) is run ONCE and skipped on re-run if demo
    sales already exist. To reseed from scratch, use a fresh dev database.

Usage
    python manage.py seed_demo_v2 --confirm
"""
import random
from datetime import datetime, timedelta, time
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction
from django.db.models import F, Sum
from django.utils import timezone as dj_tz

from canteen.models import (
    BusinessProfile, ItemCategory, Item, VariantGroup, VariantOption,
    CategoryVariantGroup, IngredientUnit, Supplier, Ingredient,
    IngredientRestockLog, RecipeIngredient, IngredientLog, ItemLog,
    Shift, PosTransaction, PaymentLine, ZReport,
)
from canteen.services import (
    create_pos_transaction, refund_transaction, close_shift_and_finalize_z,
    resolve_effective_variant_groups, _restore_ingredients,
)

User = get_user_model()
PHT = ZoneInfo('Asia/Manila')
CENTS = Decimal('0.01')

# ── Static catalog data ──────────────────────────────────────────────────────

UNITS = [
    ('Grams', 'g'), ('Milliliters', 'ml'), ('Pieces', 'pcs'),
]

SUPPLIERS = [
    ('Bulacan Coffee Traders', 'Marco Reyes', '0917-555-1010',
     'Malolos, Bulacan', 'Beans + syrups'),
    ('Fresh Dairy Co.', 'Lena Cruz', '0918-555-2020',
     'Meycauayan, Bulacan', 'Milk + cream, delivered Mon/Thu'),
    ('MetroBake Supply', 'Tonio Santos', '0919-555-3030',
     'Malolos, Bulacan', 'Flour, sugar, disposables'),
]

# name, unit_abbr, cost_per_unit, par_level, supplier
INGREDIENTS = [
    ('Espresso Beans', 'g', '1.80', '2000', 'Bulacan Coffee Traders'),
    ('Fresh Milk', 'ml', '0.09', '5000', 'Fresh Dairy Co.'),
    ('Oat Milk', 'ml', '0.22', '2000', 'Fresh Dairy Co.'),
    ('Caramel Syrup', 'ml', '0.35', '800', 'Bulacan Coffee Traders'),
    ('Chocolate Syrup', 'ml', '0.32', '800', 'Bulacan Coffee Traders'),
    ('Tea Leaves', 'g', '0.90', '400', 'Bulacan Coffee Traders'),
    ('Whipped Cream', 'ml', '0.18', '1000', 'Fresh Dairy Co.'),
    ('Sugar', 'g', '0.06', '3000', 'MetroBake Supply'),
    ('Flour', 'g', '0.05', '4000', 'MetroBake Supply'),
    ('Paper Cups', 'pcs', '1.20', '500', 'MetroBake Supply'),
]

# Big up-front restock so recipe items stay makeable after weeks of sales; a
# couple are deliberately modest so the ingredients screen shows scarcity.
# name, qty_added, cost, days_ago
RESTOCKS = [
    ('Espresso Beans', '25000', '1.80', 12),
    ('Espresso Beans', '15000', '1.85', 4),
    ('Fresh Milk', '80000', '0.09', 12),
    ('Fresh Milk', '40000', '0.09', 3),
    ('Oat Milk', '12000', '0.22', 5),
    ('Caramel Syrup', '3000', '0.35', 9),
    ('Chocolate Syrup', '3000', '0.32', 9),
    ('Tea Leaves', '900', '0.90', 10),
    ('Whipped Cream', '4000', '0.18', 6),
    ('Sugar', '20000', '0.06', 12),
    ('Flour', '18000', '0.05', 8),
    ('Paper Cups', '4000', '1.20', 12),
]

# category name, emoji
CATEGORIES = [
    ('Espresso Bar', '☕'),
    ('Non-Coffee', '🧋'),
    ('Pastries', '🥐'),
    ('Meals', '🍽️'),
    ('Retail', '🛍️'),
]

# Variant groups. selection_type, is_required, min, max, options[(name, modifier)]
VARIANT_GROUPS = {
    'Size': ('single', True, None, None,
             [('Regular', '0'), ('Large', '30')]),
    'Milk': ('single', False, None, None,
             [('Regular Milk', '0'), ('Oat Milk', '20')]),
    'Add-ons': ('multi', False, 0, 3,
                [('Extra Shot', '25'), ('Whipped Cream', '15'),
                 ('Caramel Drizzle', '10')]),
}

# Variant groups applied to these categories (CategoryVariantGroup).
CATEGORY_VARIANTS = {
    'Espresso Bar': ['Size', 'Milk', 'Add-ons'],
    'Non-Coffee': ['Size', 'Add-ons'],
}

# sku, name, category, price, cost, stock, low_threshold, zero_rated
# Stock starts generous so weeks of random sales never hit "insufficient
# stock"; the deliberate low/out-of-stock states are set at the END by
# _finalize_stock_states so the dashboard/inventory widgets always show them.
ITEMS = [
    ('LAT', 'Cafe Latte', 'Espresso Bar', '140', '55', 600, 15, False),
    ('CAP', 'Cappuccino', 'Espresso Bar', '135', '52', 600, 15, False),
    ('CML', 'Caramel Macchiato', 'Espresso Bar', '165', '68', 600, 15, False),
    ('AME', 'Americano', 'Espresso Bar', '110', '35', 600, 15, False),
    ('MOC', 'Cafe Mocha', 'Espresso Bar', '160', '65', 600, 15, False),
    ('TEA', 'Brewed Tea', 'Non-Coffee', '95', '25', 600, 12, False),
    ('MAT', 'Matcha Latte', 'Non-Coffee', '150', '60', 600, 12, False),
    ('CRO', 'Butter Croissant', 'Pastries', '85', '38', 600, 10, False),
    ('CAK', 'Chocolate Cake Slice', 'Pastries', '120', '55', 600, 10, False),
    ('MUF', 'Blueberry Muffin', 'Pastries', '90', '40', 600, 10, False),
    ('RIC', 'Chicken Rice Bowl', 'Meals', '180', '95', 600, 8, False),
    ('PAS', 'Carbonara', 'Meals', '195', '105', 600, 8, False),
    ('WAT', 'Bottled Water', 'Retail', '30', '15', 600, 20, True),
    ('BNS', 'Coffee Beans 250g (Retail)', 'Retail', '450', '260', 600, 8, True),
]

# Recipes. Each tuple: (sku, variant_option_name or None, ingredient, qty, mode)
# variant_option_name None → item-level (base) line. A name → per-item variant
# line (BUG-013: variant lines still carry the item). mode = replace|add.
RECIPES = [
    # Cafe Latte: full base recipe → makeable 'ok'. Variant lines exercise both
    # depletion modes: Large ADDS milk+shot; Oat Milk REPLACES the base milk.
    ('LAT', None, 'Espresso Beans', '18', 'replace'),
    ('LAT', None, 'Fresh Milk', '150', 'replace'),
    ('LAT', None, 'Paper Cups', '1', 'replace'),
    ('LAT', 'Large', 'Fresh Milk', '100', 'add'),
    ('LAT', 'Large', 'Espresso Beans', '9', 'add'),
    ('LAT', 'Oat Milk', 'Oat Milk', '150', 'replace'),
    # Cappuccino: full base recipe → makeable 'ok'.
    ('CAP', None, 'Espresso Beans', '18', 'replace'),
    ('CAP', None, 'Fresh Milk', '120', 'replace'),
    ('CAP', None, 'Paper Cups', '1', 'replace'),
    # Caramel Macchiato: caramel line has quantity 0 → makeable 'incomplete'.
    ('CML', None, 'Espresso Beans', '18', 'replace'),
    ('CML', None, 'Fresh Milk', '150', 'replace'),
    ('CML', None, 'Caramel Syrup', '0', 'replace'),  # deliberate incomplete
    # Americano + Mocha: valid recipes.
    ('AME', None, 'Espresso Beans', '18', 'replace'),
    ('AME', None, 'Paper Cups', '1', 'replace'),
    ('MOC', None, 'Espresso Beans', '18', 'replace'),
    ('MOC', None, 'Fresh Milk', '120', 'replace'),
    ('MOC', None, 'Chocolate Syrup', '20', 'replace'),
    # Brewed Tea: single scarce ingredient → low makeable after sales.
    ('TEA', None, 'Tea Leaves', '5', 'replace'),
    ('TEA', None, 'Paper Cups', '1', 'replace'),
    ('MAT', None, 'Fresh Milk', '120', 'replace'),
    # Chocolate Cake: recipe present.
    ('CAK', None, 'Flour', '80', 'replace'),
    ('CAK', None, 'Sugar', '40', 'replace'),
    # Butter Croissant, meals, water, beans: intentionally NO recipe →
    # makeable 'no_recipe' (pure stocked goods tracked by Item.stock only).
]

# username, role, allowed_pages (None = role default), pin
USERS = [
    ('admin', 'admin', None, '1234'),
    # Manager with a RESTRICTED override: default manager pages MINUS the
    # reports, so page-access gating is visibly narrower than the role default.
    ('manager', 'manager', ['inventory', 'ingredients', 'dashboard'], '2345'),
    # cashier1 gets an override GRANTING inventory (beyond the cashier default
    # of pos-only) — the other direction of the same gating mechanism.
    ('cashier1', 'cashier', ['inventory'], '1111'),
    # cashier2 is a plain cashier (pos only) — the baseline.
    ('cashier2', 'cashier', None, '2222'),
]

DEMO_BUSINESS_NAME = 'Tarsier Demo Cafe'


class Command(BaseCommand):
    help = ('Seed a complete, report-populating demo dataset (dev/demo only). '
            'Sales route through the real create_pos_transaction path.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm', action='store_true',
            help='Required to actually run. Without it, prints intent and aborts.',
        )
        parser.add_argument(
            '--days', type=int, default=24,
            help='Days of completed history to seed (default 24 → ≥3 Sat–Fri weeks).',
        )

    # ── entrypoint ───────────────────────────────────────────────────────────
    def handle(self, *args, **options):
        if not options['confirm']:
            existing = BusinessProfile.objects.first()
            self.stdout.write(self.style.WARNING(
                'seed_demo_v2 will create/overwrite the demo BusinessProfile and '
                'seed catalog + multi-day sales through the real POS path.'
            ))
            if existing and existing.business_name not in ('', 'My Store', DEMO_BUSINESS_NAME):
                self.stdout.write(self.style.WARNING(
                    f'  A BusinessProfile named {existing.business_name!r} already '
                    f'exists — this looks like real data. Aborting.'
                ))
            self.stdout.write(self.style.WARNING(
                'Aborting. Re-run with --confirm to proceed (dev/demo DB only).'
            ))
            return

        self.rng = random.Random(20260701)  # deterministic

        self.stdout.write('── Business profile ──')
        self._seed_business_profile()
        self.stdout.write('── Units / suppliers / ingredients ──')
        ingredients = self._seed_ingredients()
        self.stdout.write('── Categories + variant groups ──')
        cats = self._seed_categories()
        groups = self._seed_variant_groups()
        self._assign_category_variants(cats, groups)
        self.stdout.write('── Items ──')
        items = self._seed_items(cats)
        self.stdout.write('── Recipes (item-level + variant-scoped) ──')
        self._seed_recipes(items, ingredients)
        self.stdout.write('── Users ──')
        users = self._seed_users()

        # ── Phase 2: run-once dynamic data ──
        if PosTransaction.objects.filter(
            cashier__username__in=['cashier1', 'cashier2']
        ).exists():
            self.stdout.write(self.style.WARNING(
                '── Sales already seeded — skipping restock + transactions '
                '(use a fresh DB to reseed). ──'
            ))
        else:
            self.stdout.write('── Restock logs (backdated) ──')
            self._seed_restocks(ingredients, users)
            self.stdout.write('── Multi-day sales + Z finalization ──')
            self._seed_history(items, users, options['days'])
            self.stdout.write('── Live (open) shift for today ──')
            self._seed_live_today(items, users)
            self.stdout.write('── Final stock states (low-stock + makeable) ──')
            self._finalize_stock_states(items, ingredients)

        self._summary()

    # ── static seeders ───────────────────────────────────────────────────────
    def _seed_business_profile(self):
        bp = BusinessProfile.objects.first() or BusinessProfile()
        bp.business_name = DEMO_BUSINESS_NAME
        bp.tagline = 'Your neighborhood coffee spot'
        bp.address = 'Malolos, Bulacan, Philippines'
        bp.contact_number = '0917-123-4567'
        bp.email = 'hello@tarsierdemo.ph'
        bp.tin = '123-456-789-000'
        bp.currency = 'PHP'
        bp.receipt_header = 'Welcome to Tarsier Demo Cafe!'
        bp.receipt_footer = 'Thank you! Please come again!'
        bp.track_inventory = True
        bp.discounts_enabled = True
        bp.vat_enabled = True
        bp.vat_rate = Decimal('12.00')
        bp.vat_inclusive = True
        bp.sc_discount_enabled = True
        bp.sc_discount_rate = Decimal('20.00')
        bp.pwd_discount_enabled = True
        bp.pwd_discount_rate = Decimal('20.00')
        bp.promo_discount_enabled = False
        bp.printer_mode = 'disabled'
        # BIR identity present → finalized Z reports resolve is_official=True.
        bp.machine_identification_number = 'MIN-DEMO-0001'
        bp.machine_serial_number = 'SN-DEMO-0001'
        bp.pos_accreditation_number = 'ACCR-DEMO-0001'
        bp.pos_permit_number = 'PTU-DEMO-0001'
        bp.save()

    def _seed_ingredients(self):
        units = {}
        for name, abbr in UNITS:
            u, _ = IngredientUnit.objects.get_or_create(
                abbreviation=abbr, defaults={'name': name, 'is_active': True})
            units[abbr] = u
        suppliers = {}
        for name, contact, phone, address, notes in SUPPLIERS:
            s, _ = Supplier.objects.get_or_create(
                name=name, defaults={'contact_person': contact, 'phone': phone,
                                     'address': address, 'notes': notes})
            suppliers[name] = s
        ingredients = {}
        for name, abbr, cost, par, sup in INGREDIENTS:
            ing, _ = Ingredient.objects.get_or_create(
                name=name,
                defaults={'unit': units[abbr], 'cost_per_unit': Decimal(cost),
                          'par_level': Decimal(par), 'supplier': suppliers.get(sup),
                          'current_stock': Decimal('0'), 'track_depletion': True})
            ingredients[name] = ing
        return ingredients

    def _seed_categories(self):
        cats = {}
        for name, emoji in CATEGORIES:
            c, _ = ItemCategory.objects.get_or_create(
                name=name, defaults={'emoji': emoji, 'is_active': True})
            cats[name] = c
        return cats

    def _seed_variant_groups(self):
        groups = {}
        for gname, (stype, req, mn, mx, opts) in VARIANT_GROUPS.items():
            g, created = VariantGroup.objects.get_or_create(
                name=gname,
                defaults={'selection_type': stype, 'is_required': req,
                          'min_selections': mn, 'max_selections': mx})
            for i, (oname, mod) in enumerate(opts):
                VariantOption.objects.get_or_create(
                    group=g, name=oname,
                    defaults={'price_modifier': Decimal(mod), 'sort_order': i})
            groups[gname] = g
        return groups

    def _assign_category_variants(self, cats, groups):
        for cat_name, group_names in CATEGORY_VARIANTS.items():
            for gname in group_names:
                CategoryVariantGroup.objects.get_or_create(
                    category=cats[cat_name], group=groups[gname])

    def _seed_items(self, cats):
        items = {}
        for sku, name, cat, price, cost, stock, low, zero in ITEMS:
            it, _ = Item.objects.get_or_create(
                sku=sku,
                defaults={'name': name, 'category': cats[cat],
                          'price': Decimal(price), 'purchase_price': Decimal(cost),
                          'stock': stock, 'low_stock_threshold': low,
                          'zero_rated': zero, 'is_active': True})
            items[sku] = it
        return items

    def _seed_recipes(self, items, ingredients):
        # Build (sku -> {option_name -> VariantOption}) for variant lines.
        for sku, opt_name, ing_name, qty, mode in RECIPES:
            item = items[sku]
            ingredient = ingredients[ing_name]
            variant = None
            if opt_name is not None:
                variant = VariantOption.objects.filter(name=opt_name).first()
            RecipeIngredient.objects.get_or_create(
                item=item, variant=variant, ingredient=ingredient,
                defaults={'quantity_used': Decimal(qty), 'depletion_mode': mode})

    def _seed_users(self):
        users = {}
        for username, role, pages, pin in USERS:
            u, _ = User.objects.get_or_create(
                username=username, defaults={'is_active': True})
            u.role = role
            u.allowed_pages = pages
            u.is_active = True
            if role == 'admin':
                u.is_staff = True
                u.is_superuser = True
            u.set_password(pin)
            u.save()
            users[username] = u
        return users

    def _seed_restocks(self, ingredients, users):
        recorder = users['manager']
        now = dj_tz.now()
        for name, qty, cost, days_ago in RESTOCKS:
            log = IngredientRestockLog(
                ingredient=ingredients[name], quantity_added=Decimal(qty),
                cost_per_unit=Decimal(cost),
                notes='Demo restock', recorded_by=recorder)
            log.save()  # bumps current_stock on insert
            when = now - timedelta(days=days_ago)
            when = when.astimezone(PHT).replace(hour=8, minute=0).astimezone(dj_tz.get_current_timezone())
            IngredientRestockLog.objects.filter(pk=log.pk).update(date=when)

    # ── sales engine ─────────────────────────────────────────────────────────
    def _auto_variants(self, item):
        """Satisfy all required groups, add some optional ones. Returns
        (selections, modifier_total)."""
        sels, modifier = [], Decimal('0')
        for r in resolve_effective_variant_groups(item):
            g, req = r['group'], r['required']
            opts = list(g.options.filter(is_active=True))
            if not opts:
                continue
            if g.selection_type == 'single':
                if req or self.rng.random() < 0.7:
                    o = self.rng.choice(opts)
                    sels.append({'group_id': str(g.id), 'option_id': str(o.id)})
                    modifier += o.price_modifier
            else:  # multi — respect min/max cardinality
                lo = g.min_selections or 0
                hi = g.max_selections if g.max_selections is not None else len(opts)
                hi = min(hi, len(opts))
                k = self.rng.randint(lo, hi) if hi >= lo else lo
                for o in self.rng.sample(opts, k=k):
                    sels.append({'group_id': str(g.id), 'option_id': str(o.id)})
                    modifier += o.price_modifier
        return sels, modifier

    def _ring(self, cashier, cart, when_dt, discount_type='none',
              discount_id='', payment='cash', split=False):
        """Ring one sale through the real path, then backdate it.

        cart: list of (Item, qty). Variants auto-resolved. when_dt is an aware
        datetime for created_at (and its ingredient-ledger rows).
        """
        items_data, total = [], Decimal('0')
        for item, qty in cart:
            sels, modifier = self._auto_variants(item)
            line_unit = Decimal(str(item.price)) + modifier
            total += line_unit * qty
            items_data.append({'item_id': str(item.id), 'quantity': qty,
                               'variant_selections': sels})

        kwargs = {}
        # SC/PWD discount — compute exactly as create_pos_transaction re-derives
        # it (VAT-exclusive base × 20%) so validation passes.
        if discount_type in ('sc', 'pwd'):
            vat_exclusive = (total / Decimal('1.12')).quantize(CENTS, ROUND_HALF_UP)
            disc = (vat_exclusive * Decimal('0.20')).quantize(CENTS, ROUND_HALF_UP)
            kwargs['discount_amount'] = disc
            kwargs['discount_type'] = discount_type
            kwargs['discount_id_number'] = discount_id
            charged = total - (total - vat_exclusive) - disc
        else:
            charged = total

        charged = charged.quantize(CENTS, ROUND_HALF_UP)
        if split:
            half = (charged / 2).quantize(CENTS, ROUND_HALF_UP)
            kwargs['payment_lines'] = [
                {'method': 'cash', 'amount': str(half)},
                {'method': 'gcash', 'amount': str(charged - half)},
            ]
            payment = 'cash'
        elif payment == 'cash':
            kwargs['cash_received'] = (charged.to_integral_value(ROUND_HALF_UP)
                                       + Decimal('50'))
        elif payment == 'gcash':
            kwargs['gcash_reference'] = f'GC{self.rng.randint(10**7, 10**8 - 1)}'
        elif payment == 'maya':
            kwargs['maya_reference'] = f'MY{self.rng.randint(10**7, 10**8 - 1)}'
        elif payment == 'card':
            kwargs['card_reference'] = f'CARD{self.rng.randint(1000, 9999)}'

        txn = create_pos_transaction(items_data, payment, cashier=cashier, **kwargs)
        # Backdate the sale and its ingredient-ledger rows to the historical day.
        PosTransaction.objects.filter(pk=txn.pk).update(created_at=when_dt)
        IngredientLog.objects.filter(transaction=txn).update(timestamp=when_dt)
        return txn

    def _void(self, txn, voider, when_dt):
        """Replicate the void view (services has no void fn): restore stock +
        ingredients + ItemLog, flag the transaction, backdate voided_at."""
        for line in txn.items.all():
            Item.objects.filter(pk=line.item_id).update(
                stock=F('stock') + line.quantity)
            _restore_ingredients(line.item, line, line.quantity,
                                 transaction=txn, performed_by=voider)
            refreshed = Item.objects.get(pk=line.item_id)
            ItemLog.objects.create(
                item=refreshed, quantity=line.quantity,
                current_stock=refreshed.stock, action='return',
                remarks=f'Void reversal — {txn.transaction_no}', created_by=voider)
        txn.void = True
        txn.voided_at = when_dt
        txn.status = 'void'
        txn.voided_by = voider
        txn.purpose_of_void = 'Customer changed order (demo)'
        txn.save(update_fields=['void', 'voided_at', 'status', 'voided_by',
                                'purpose_of_void', 'updated_at'])
        IngredientLog.objects.filter(transaction=txn, action='void').update(
            timestamp=when_dt)

    def _open_shift(self, cashier, day, opening_cash):
        shift = Shift.objects.create(cashier=cashier,
                                     opening_cash=Decimal(str(opening_cash)))
        opened = datetime.combine(day, time(8, 0), tzinfo=PHT)
        Shift.objects.filter(pk=shift.pk).update(opened_at=opened)
        shift.refresh_from_db()
        return shift

    def _close_shift(self, shift, cashier, day):
        cash_collected = PaymentLine.objects.filter(
            transaction__shift=shift, transaction__void=False, method='cash'
        ).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        expected = Decimal(str(shift.opening_cash)) + cash_collected
        counted = expected + Decimal(str(self.rng.choice([-20, -10, 0, 0, 0, 10, 25])))
        z = close_shift_and_finalize_z(shift.id, counted, cashier)
        closed = datetime.combine(day, time(18, 30), tzinfo=PHT)
        Shift.objects.filter(pk=shift.pk).update(closed_at=closed)
        ZReport.objects.filter(pk=z.pk).update(finalized_at=closed)
        return z

    def _random_cart(self, items):
        pool = list(items.values())
        n = self.rng.randint(1, 3)
        cart = []
        for _ in range(n):
            cart.append((self.rng.choice(pool), self.rng.randint(1, 2)))
        return cart

    def _rand_time(self, day):
        return datetime.combine(
            day, time(self.rng.randint(8, 17), self.rng.randint(0, 59),
                      self.rng.randint(0, 59)), tzinfo=PHT)

    def _seed_history(self, items, users, days):
        today = dj_tz.localdate()
        cashiers = [users['cashier1'], users['cashier2']]
        payments = ['cash', 'cash', 'cash', 'gcash', 'gcash', 'maya', 'card']
        beverages = [i for k, i in items.items()
                     if i.category.name in ('Espresso Bar', 'Non-Coffee')]
        food = [i for k, i in items.items()
                if i.category.name in ('Pastries', 'Meals')]
        water = items['WAT']
        z_count = txn_count = 0

        for offset in range(days, 0, -1):
            day = today - timedelta(days=offset)
            weekend = day.weekday() >= 5
            for cashier in cashiers:
                shift = self._open_shift(cashier, day, opening_cash=1000)
                daily = self.rng.randint(8, 14) if weekend else self.rng.randint(5, 9)
                for _ in range(daily):
                    cart = self._random_cart(items)
                    pay = self.rng.choice(payments)
                    self._ring(cashier, cart, self._rand_time(day), payment=pay)
                    txn_count += 1

                # Sprinkle special transactions on recent days (cashier1 only) so
                # they land inside the last two Sat–Fri weeks + the prior week.
                if cashier == cashiers[0] and offset in (2, 3, 9, 10):
                    if offset in (2, 9):  # SC discount + a void + a zero-rated sale
                        self._ring(cashier, [(self.rng.choice(beverages), 2)],
                                   self._rand_time(day), discount_type='sc',
                                   discount_id='SC-1234567', payment='cash')
                        v = self._ring(cashier, [(self.rng.choice(food), 1)],
                                       self._rand_time(day), payment='gcash')
                        self._void(v, users['manager'], self._rand_time(day))
                        self._ring(cashier, [(water, 3)], self._rand_time(day),
                                   payment='cash')
                        txn_count += 3
                    if offset in (3, 10):  # PWD discount + split + a refund
                        self._ring(cashier, [(self.rng.choice(food), 1)],
                                   self._rand_time(day), discount_type='pwd',
                                   discount_id='PWD-7654321', payment='cash')
                        self._ring(cashier, [(self.rng.choice(food), 2)],
                                   self._rand_time(day), split=True)
                        orig = self._ring(cashier,
                                          [(self.rng.choice(beverages), 1)],
                                          self._rand_time(day), payment='gcash')
                        refund = refund_transaction(orig.id, performed_by=cashier)
                        PosTransaction.objects.filter(pk=refund.pk).update(
                            created_at=self._rand_time(day))
                        txn_count += 3

                self._close_shift(shift, cashier, day)
                z_count += 1
        self._hist = (txn_count, z_count)

    def _seed_live_today(self, items, users):
        today = dj_tz.localdate()
        cashier = users['cashier1']
        shift = Shift.objects.create(cashier=cashier, opening_cash=Decimal('1000'))
        # opened_at = now (real open shift for today), left OPEN (not finalized)
        for _ in range(self.rng.randint(4, 7)):
            cart = self._random_cart(items)
            pay = self.rng.choice(['cash', 'gcash', 'maya', 'card'])
            # no backdate → created_at = now = today (live)
            items_data, _t = [], Decimal('0')
            for item, qty in cart:
                sels, _m = self._auto_variants(item)
                items_data.append({'item_id': str(item.id), 'quantity': qty,
                                   'variant_selections': sels})
            kwargs = {}
            if pay == 'cash':
                kwargs['cash_received'] = Decimal('1000')
            elif pay == 'gcash':
                kwargs['gcash_reference'] = f'GC{self.rng.randint(10**7, 10**8 - 1)}'
            elif pay == 'maya':
                kwargs['maya_reference'] = f'MY{self.rng.randint(10**7, 10**8 - 1)}'
            else:
                kwargs['card_reference'] = f'CARD{self.rng.randint(1000, 9999)}'
            create_pos_transaction(items_data, pay, cashier=cashier, **kwargs)
        self._live_shift = shift

    def _finalize_stock_states(self, items, ingredients):
        # Deterministic low-stock + out-of-stock so the dashboard/inventory
        # widgets always show them, independent of random depletion.
        Item.objects.filter(pk=items['MUF'].pk).update(stock=4)   # low
        Item.objects.filter(pk=items['CAK'].pk).update(stock=6)   # low
        Item.objects.filter(pk=items['BNS'].pk).update(stock=0)   # out of stock
        # Make one ingredient scarce so an item's ingredient-derived makeable is
        # low, and confirm the healthy ones stay makeable.
        Ingredient.objects.filter(pk=ingredients['Tea Leaves'].pk).update(
            current_stock=Decimal('8'))  # Brewed Tea makeable ≈ 1

    def _summary(self):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('seed_demo_v2 complete.'))
        self.stdout.write(f'  Items:        {Item.objects.count()}')
        self.stdout.write(f'  Ingredients:  {Ingredient.objects.count()}')
        self.stdout.write(f'  Recipes:      {RecipeIngredient.objects.count()}')
        self.stdout.write(f'  Users:        {User.objects.count()}')
        self.stdout.write(f'  Transactions: {PosTransaction.objects.count()} '
                          f'(is_seed=False — visible in all reports)')
        self.stdout.write(f'  Z reports:    {ZReport.objects.count()}')
        self.stdout.write('')
        self.stdout.write('  Demo logins (username / PIN):')
        for username, role, _pages, pin in USERS:
            self.stdout.write(f'    {role:<8} {username:<9} {pin}')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            '  DEV/DEMO ONLY — do NOT deploy this DB to a client box.'))
