"""
seed_demo — idempotent demo-data seeder for Tarsier Demo Cafe.

Usage:
    python manage.py seed_demo

Safe to run multiple times — skips anything that already exists.
"""
import random
from datetime import date, timedelta, timezone as dt_tz, datetime
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone as dj_tz
from djmoney.money import Money

User = get_user_model()
PHT = dt_tz(timedelta(hours=8))


# ── Products ──────────────────────────────────────────────────────────────────
# (category_name, sku, name, price, cost, image_filename)
PRODUCTS = [
    # Hot Coffee
    ('Hot Coffee',   'DEMO-HC-001', 'Espresso',            80,  20, 'espresso.png'),
    ('Hot Coffee',   'DEMO-HC-002', 'Cappuccino',          120, 35, 'cappuccino.png'),
    ('Hot Coffee',   'DEMO-HC-003', 'Cafe Latte',          130, 40, 'cafe_latte.png'),
    ('Hot Coffee',   'DEMO-HC-004', 'Caramel Macchiato',   155, 45, 'caramel_macchiato.png'),
    ('Hot Coffee',   'DEMO-HC-005', 'Hot Chocolate',       110, 30, 'hot_chocolate.png'),
    ('Hot Coffee',   'DEMO-HC-006', 'Hot Mocha',           130, 40, 'hot_mocha.png'),
    ('Hot Coffee',   'DEMO-HC-007', 'Matcha Latte',        140, 45, 'matcha_latte.png'),
    ('Hot Coffee',   'DEMO-HC-008', 'Chamomile Tea',        90, 20, 'chamomile_tea.png'),
    # Iced Coffee
    ('Iced Coffee',  'DEMO-IC-001', 'Iced Latte',          140, 40, 'iced_latte.png'),
    ('Iced Coffee',  'DEMO-IC-002', 'Iced Americano',      120, 35, 'iced_americano.png'),
    ('Iced Coffee',  'DEMO-IC-003', 'Iced Mocha',          145, 45, 'iced_mocha.png'),
    ('Iced Coffee',  'DEMO-IC-004', 'Nitro Cold Brew',     160, 50, 'nitro_cold_brew.png'),
    ('Iced Coffee',  'DEMO-IC-005', 'Caramel Milk Tea',    130, 35, 'caramel_milk_tea.png'),
    ('Iced Coffee',  'DEMO-IC-006', 'Caramel Frappe',      150, 45, 'caramel_frappe.png'),
    # Non-Coffee (Smoothies)
    ('Non-Coffee',   'DEMO-NC-001', 'Mango Smoothie',      130, 35, 'mango_smoothie.png'),
    ('Non-Coffee',   'DEMO-NC-002', 'Strawberry Smoothie', 130, 35, 'strawberry_smoothie.png'),
    ('Non-Coffee',   'DEMO-NC-003', 'Mint Lemonade',       110, 25, 'mint_lemonade.png'),
    # Pastries
    ('Pastries',     'DEMO-PA-001', 'Butter Croissant',     85, 30, 'butter_croissant.png'),
    ('Pastries',     'DEMO-PA-002', 'Blueberry Muffin',     90, 25, 'blueberry_muffin.png'),
    ('Pastries',     'DEMO-PA-003', 'Cream Cheese Danish',  95, 30, 'cream_cheese_danish.png'),
    ('Pastries',     'DEMO-PA-004', 'Banana Bread',         80, 20, 'banana_bread.png'),
    ('Pastries',     'DEMO-PA-005', 'Dinner Rolls',         60, 15, 'dinner_rolls.png'),
    # All-Day Meals
    ('All-Day Meals','DEMO-AD-001', 'Eggs Benedict',       220, 90, 'eggs_benedict.png'),
    ('All-Day Meals','DEMO-AD-002', 'Chicken Club Sandwich',185,75, 'chicken_club_sandwich.png'),
    ('All-Day Meals','DEMO-AD-003', 'BLT Sandwich',        160, 65, 'blt_sandwich.png'),
    ('All-Day Meals','DEMO-AD-004', 'Croque Monsieur',     195, 80, 'croque_monsieur.png'),
    ('All-Day Meals','DEMO-AD-005', 'Spaghetti Carbonara', 210, 85, 'spaghetti_carbonara.png'),
    ('All-Day Meals','DEMO-AD-006', 'Chicken Pesto Pasta', 215, 85, 'chicken_pesto_pasta.png'),
    ('All-Day Meals','DEMO-AD-007', 'Caesar Salad',        160, 60, 'caesar_salad.png'),
    # Desserts
    ('Desserts',     'DEMO-DE-001', 'Chocolate Lava Cake', 155, 50, 'chocolate_lava_cake.png'),
    ('Desserts',     'DEMO-DE-002', 'Tiramisu',            160, 55, 'tiramisu.png'),
    ('Desserts',     'DEMO-DE-003', 'Strawberry Cheesecake',150,50, 'strawberry_cheesecake.png'),
    ('Desserts',     'DEMO-DE-004', 'Leche Flan',          120, 35, 'leche_flan.png'),
    ('Desserts',     'DEMO-DE-005', 'Mango Crepe',         140, 45, 'mango_crepe.png'),
    ('Desserts',     'DEMO-DE-006', 'Belgian Waffle',      145, 45, 'belgian_waffle.png'),
]

# ── Category metadata ──────────────────────────────────────────────────────────
CATEGORIES = [
    ('Hot Coffee',    '☕', 'Espresso-based and brewed hot coffee'),
    ('Iced Coffee',   '🧋', 'Cold and blended coffee drinks'),
    ('Non-Coffee',    '🍵', 'Smoothies, tea, lemonade, and fruit drinks'),
    ('Pastries',      '🥐', 'Fresh-baked pastries and bread'),
    ('All-Day Meals', '🍽️', 'Sandwiches, pasta, and mains served all day'),
    ('Desserts',      '🍮', 'Sweet treats and desserts'),
]

# ── Variant assignments per category ──────────────────────────────────────────
# (group_name, is_required_override)  None = inherit group default
VARIANT_ASSIGNMENTS = {
    'Hot Coffee':    [('Temperature', True), ('Size', None), ('Sweetness', None),
                      ('Milk', None), ('Additionals', None)],
    'Iced Coffee':   [('Size', True), ('Sweetness', None), ('Milk', None),
                      ('Additionals', None)],
    'Non-Coffee':    [('Size', None), ('Sweetness', None), ('Milk', None)],
    'All-Day Meals': [('Spice Level', None)],
}

# ── Demo users ─────────────────────────────────────────────────────────────────
DEMO_USERS = [
    # (username, password, role, first_name, last_name, employee_id)
    ('cashier1', 'demo1234', 'cashier', 'Maria',  'Santos', 'EMP-001'),
    ('cashier2', 'demo1234', 'cashier', 'Jose',   'Reyes',  'EMP-002'),
    ('manager1', 'demo1234', 'manager', 'Ana',    'Cruz',   'EMP-003'),
]

# Payment method weights: 60% cash, 25% gcash, 15% maya
PAYMENT_WEIGHTS = [('cash', 60), ('gcash', 25), ('maya', 15)]
PAYMENT_CHOICES, PAYMENT_W = zip(*PAYMENT_WEIGHTS)

# Morning shift: 7–15, Afternoon shift: 15–23 (PHT hour range as int)
SHIFTS = [
    (7, 15),   # morning
    (15, 23),  # afternoon
]

SC_ID_POOL  = [f'SC-{10000000 + i * 1234567 % 90000000:08d}' for i in range(20)]
PWD_ID_POOL = [f'PWD-{20000000 + i * 7654321 % 80000000:08d}' for i in range(20)]


class Command(BaseCommand):
    help = 'Seed demo data for Tarsier Demo Cafe (idempotent)'

    def handle(self, *args, **options):
        from canteen.models import (
            ItemCategory, Item, VariantGroup, CategoryVariantGroup,
            PosTransaction, PosTransactionItem, TransactionItemVariant,
            EmployeeProfile, BusinessProfile,
        )

        self.stdout.write('── Business profile ─────────────────')
        self._seed_business_profile(BusinessProfile)

        self.stdout.write('── Categories ───────────────────────')
        cats = self._seed_categories(ItemCategory)

        self.stdout.write('── Products ─────────────────────────')
        items, missing_images = self._seed_items(Item, cats)

        self.stdout.write('── Variant assignments ──────────────')
        self._seed_variant_assignments(CategoryVariantGroup, VariantGroup, cats)

        self.stdout.write('── Users ────────────────────────────')
        users = self._seed_users(EmployeeProfile)

        self.stdout.write('── Transactions ─────────────────────')
        txn_count = self._seed_transactions(
            PosTransaction, PosTransactionItem, TransactionItemVariant,
            VariantGroup, items, users,
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. Products created: {len(items)} | '
            f'Transactions created: {txn_count} | '
            f'Missing images: {len(missing_images)}'
        ))
        if missing_images:
            for img in missing_images:
                self.stdout.write(self.style.WARNING(f'  Missing image: {img}'))

    # ── Business profile ───────────────────────────────────────────────────────
    def _seed_business_profile(self, BusinessProfile):
        profile, created = BusinessProfile.objects.get_or_create(id=1)
        profile.business_name = 'Tarsier Demo Cafe'
        profile.tagline = 'Your neighborhood coffee spot'
        profile.address = 'Malolos, Bulacan, Philippines'
        profile.contact_number = '0917-123-4567'
        profile.email = 'hello@tarsiercafe.ph'
        profile.tin = '123-456-789-000'
        profile.receipt_header = 'Welcome to Tarsier Demo Cafe!'
        profile.receipt_footer = 'Thank you! See you again soon!'
        profile.save()
        self.stdout.write(f'  {"Created" if created else "Updated"}: {profile.business_name}')

    # ── Categories ─────────────────────────────────────────────────────────────
    def _seed_categories(self, ItemCategory):
        cats = {}
        for name, emoji, desc in CATEGORIES:
            cat, created = ItemCategory.objects.get_or_create(
                name=name,
                defaults={'emoji': emoji, 'description': desc, 'is_active': True},
            )
            cats[name] = cat
            self.stdout.write(f'  {"Created" if created else "Exists"}: {name}')
        return cats

    # ── Products ───────────────────────────────────────────────────────────────
    def _seed_items(self, Item, cats):
        import os
        from django.conf import settings
        created_items = []
        missing = []
        for cat_name, sku, name, price, cost, img_file in PRODUCTS:
            img_path = f'products/{img_file}'
            full_path = os.path.join(settings.MEDIA_ROOT, img_path)
            if not os.path.exists(full_path):
                missing.append(img_path)
                img_path = ''
            item, created = Item.objects.get_or_create(
                sku=sku,
                defaults={
                    'category': cats[cat_name],
                    'name': name,
                    'price': price,
                    'purchase_price': cost,
                    'stock': 100,
                    'low_stock_threshold': 10,
                    'photo': img_path,
                    'is_active': True,
                },
            )
            created_items.append(item)
            self.stdout.write(f'  {"Created" if created else "Exists"}: {sku} – {name}')
        return created_items, missing

    # ── Variant assignments ────────────────────────────────────────────────────
    def _seed_variant_assignments(self, CategoryVariantGroup, VariantGroup, cats):
        vg_cache = {vg.name: vg for vg in VariantGroup.objects.all()}
        for cat_name, assignments in VARIANT_ASSIGNMENTS.items():
            cat = cats.get(cat_name)
            if not cat:
                continue
            for group_name, req_override in assignments:
                vg = vg_cache.get(group_name)
                if not vg:
                    self.stdout.write(self.style.WARNING(
                        f'  VariantGroup not found: {group_name}'))
                    continue
                _, created = CategoryVariantGroup.objects.get_or_create(
                    category=cat,
                    group=vg,
                    defaults={'is_required_override': req_override},
                )
                self.stdout.write(
                    f'  {"Created" if created else "Exists"}: {cat_name} → {group_name}'
                    + (f' (required)' if req_override is True else '')
                )

    # ── Users ──────────────────────────────────────────────────────────────────
    def _seed_users(self, EmployeeProfile):
        users = []
        for username, password, role, first, last, emp_id in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first,
                    'last_name': last,
                    'is_active': True,
                },
            )
            if created:
                user.set_password(password)
                user.save()
            EmployeeProfile.objects.get_or_create(
                user=user,
                defaults={'role': role, 'employee_id': emp_id},
            )
            users.append(user)
            self.stdout.write(
                f'  {"Created" if created else "Exists"}: {username} ({role})')
        return users

    # ── Transactions ───────────────────────────────────────────────────────────
    def _seed_transactions(
        self, PosTransaction, PosTransactionItem, TransactionItemVariant,
        VariantGroup, items, users,
    ):
        # Skip if demo transactions already exist
        if PosTransaction.objects.filter(
            cashier__username__in=['cashier1', 'cashier2']
        ).exists():
            self.stdout.write('  Transactions already seeded — skipping.')
            return 0

        rng = random.Random(42)  # deterministic seed
        today_pht = dj_tz.now().astimezone(PHT).date()

        # Build lookup structures
        bev_categories = {'Hot Coffee', 'Iced Coffee', 'Non-Coffee'}
        bev_items = [i for i in items if i.category.name in bev_categories]
        food_items = [i for i in items if i.category.name not in bev_categories]

        # Preload variant groups and their options
        vg_options = {}
        for vg in VariantGroup.objects.prefetch_related('options').all():
            opts = [o for o in vg.options.all() if o.is_active]
            if opts:
                vg_options[vg.name] = (vg, opts)

        # CategoryVariantGroup lookup: cat_id → list of (vg, is_required)
        from canteen.models import CategoryVariantGroup
        cat_vgs = {}
        for cvg in CategoryVariantGroup.objects.select_related('group', 'category').all():
            cat_vgs.setdefault(cvg.category_id, []).append(cvg)

        cashiers = [u for u in users if
                    hasattr(u, 'employee_profile') and
                    u.employee_profile.role == 'cashier']
        if not cashiers:
            cashiers = users[:2]

        # Choose 3-5 days to have a void transaction
        days_count = 14
        void_days = set(rng.sample(range(days_count), k=rng.randint(3, 5)))

        total_created = 0
        void_budget = rng.randint(3, 5)

        for day_offset in range(days_count, 0, -1):  # 14 days ago → yesterday
            txn_date = today_pht - timedelta(days=day_offset)
            # Weekends busier
            weekday = txn_date.weekday()
            if weekday >= 5:  # Sat/Sun
                daily_count = rng.randint(25, 35)
            else:
                daily_count = rng.randint(15, 25)

            # Pick 1-2 shifts
            active_shifts = SHIFTS if rng.random() < 0.7 else [rng.choice(SHIFTS)]

            # Determine which transactions today are voided
            void_count_today = 0
            if (days_count - day_offset) in void_days and void_budget > 0:
                void_count_today = 1
                void_budget -= 1

            void_indices = set(rng.sample(range(daily_count),
                                          k=min(void_count_today, daily_count)))

            or_counter = 9001  # start at 9001 to avoid collision with real counter
            for txn_idx in range(daily_count):
                shift = rng.choice(active_shifts)
                hour = rng.randint(shift[0], shift[1] - 1)
                minute = rng.randint(0, 59)
                second = rng.randint(0, 59)
                txn_pht = datetime(
                    txn_date.year, txn_date.month, txn_date.day,
                    hour, minute, second, tzinfo=PHT,
                )
                txn_utc = txn_pht.astimezone(dt_tz.utc)

                cashier = rng.choice(cashiers)
                is_void = txn_idx in void_indices
                payment = rng.choices(PAYMENT_CHOICES, weights=PAYMENT_W, k=1)[0]

                # Discount: ~9% chance of SC/PWD
                disc_type = ''
                disc_id = ''
                if rng.random() < 0.09:
                    disc_type = rng.choice(['sc', 'pwd'])
                    disc_id = rng.choice(
                        SC_ID_POOL if disc_type == 'sc' else PWD_ID_POOL)

                # Build cart: 1-4 items
                cart = []
                num_items = rng.randint(1, 4)
                # Always include at least 1 beverage if possible
                cart_items_pool = (
                    rng.sample(bev_items, k=min(1, len(bev_items))) +
                    rng.sample(items, k=num_items - 1)
                ) if bev_items else rng.sample(items, k=num_items)
                for prod in cart_items_pool[:num_items]:
                    qty = rng.randint(1, 2)
                    unit_price = float(prod.price)
                    # Add variant price modifiers
                    variant_sels = []
                    if prod.category_id in cat_vgs:
                        for cvg in cat_vgs[prod.category_id]:
                            vg_name = cvg.group.name
                            if vg_name not in vg_options:
                                continue
                            vg, opts = vg_options[vg_name]
                            # Multi-select groups: pick 0-2
                            if vg.selection_type == 'multi':
                                chosen = rng.sample(opts, k=rng.randint(0, min(2, len(opts))))
                            else:
                                # Required: always pick; optional: 80% chance
                                req = (cvg.is_required_override
                                       if cvg.is_required_override is not None
                                       else vg.is_required)
                                if req or rng.random() < 0.8:
                                    chosen = [rng.choice(opts)]
                                else:
                                    chosen = []
                            for opt in chosen:
                                variant_sels.append({
                                    'group_name': vg_name,
                                    'option_name': opt.name,
                                    'price_modifier': float(opt.price_modifier),
                                })
                                unit_price += float(opt.price_modifier)
                    subtotal = unit_price * qty
                    cart.append((prod, qty, unit_price, subtotal, variant_sels))

                gross = sum(s for _, _, _, s, _ in cart)

                # SC/PWD: 20% discount on VAT-exclusive base
                disc_amount = Decimal('0.00')
                vat_exempt = False
                vat_amount = Decimal('0.00')
                if disc_type in ('sc', 'pwd'):
                    vat_base = Decimal(str(gross)) / Decimal('1.12')
                    disc_amount = (vat_base * Decimal('0.20')).quantize(
                        Decimal('0.01'), rounding=ROUND_HALF_UP)
                    vat_amount = (Decimal(str(gross)) - vat_base).quantize(
                        Decimal('0.01'), rounding=ROUND_HALF_UP)
                    vat_exempt = True

                total = Decimal(str(gross)) - disc_amount
                transaction_no = f'OR-{txn_date.strftime("%Y%m%d")}-{or_counter:04d}'
                or_counter += 1

                # Payment reference
                gcash_ref = maya_ref = None
                cash_received = change_given = None
                if payment == 'gcash':
                    gcash_ref = f'GC{rng.randint(10000000, 99999999)}'
                elif payment == 'maya':
                    maya_ref = f'MY{rng.randint(10000000, 99999999)}'
                else:
                    # Round up to nearest 50/100/500/1000 depending on total
                    t = float(total)
                    if t <= 50:
                        cash_received = Decimal('50')
                    elif t <= 100:
                        cash_received = Decimal('100')
                    elif t <= 200:
                        cash_received = Decimal('200')
                    elif t <= 500:
                        cash_received = Decimal('500')
                    elif t <= 1000:
                        cash_received = Decimal('1000')
                    else:
                        # Multiple 1000-peso bills
                        cash_received = Decimal(str(int(t / 1000 + 1) * 1000))
                    # Occasionally pay exact or one denomination higher
                    if rng.random() < 0.3:
                        cash_received += Decimal('100')
                    change_given = cash_received - total

                status = 'void' if is_void else 'completed'

                txn = PosTransaction(
                    transaction_no=transaction_no,
                    cashier=cashier,
                    created_by=cashier,
                    payment_method=payment,
                    status=status,
                    void=is_void,
                    discount_type=disc_type,
                    discount_amount=disc_amount,
                    discount_id_number=disc_id,
                    vat_exempt=vat_exempt,
                    vat_amount=vat_amount,
                    total_amount=Money(total, 'PHP'),
                    gcash_reference=gcash_ref,
                    maya_reference=maya_ref,
                    cash_received=cash_received,
                    change_given=change_given,
                )
                if is_void:
                    txn.purpose_of_void = 'Demo void transaction'
                txn.save()
                # Backdate created_at to historical timestamp
                PosTransaction.objects.filter(pk=txn.pk).update(created_at=txn_utc)

                for prod, qty, unit_price, subtotal, variant_sels in cart:
                    ti = PosTransactionItem.objects.create(
                        pos_transaction=txn,
                        item=prod,
                        quantity=qty,
                        unit_price=Decimal(str(unit_price)),
                        purchase_price=prod.purchase_price,
                        subtotal=Decimal(str(subtotal)),
                        base_price=Decimal(str(prod.price)),
                        final_price=Decimal(str(unit_price)),
                    )
                    for vs in variant_sels:
                        TransactionItemVariant.objects.create(
                            transaction_item=ti,
                            group_name=vs['group_name'],
                            option_name=vs['option_name'],
                            price_modifier=Decimal(str(vs['price_modifier'])),
                        )

                total_created += 1

        return total_created
