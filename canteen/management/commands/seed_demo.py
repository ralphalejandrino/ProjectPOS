from django.core.management.base import BaseCommand
from canteen.models import ItemCategory, Item, BusinessProfile


# 35 item images available in media/images/canteen/item/
IMGS = [
    'images/canteen/item/000ef8bb-8f4c-4a90-bd63-ad1b7d431cf0.png',
    'images/canteen/item/044c9e5f-3ddd-42fc-b4ec-ce5b6691ebbd.jpg',
    'images/canteen/item/0c6c3a36-eb0b-4bd5-8cb2-e0646f519e81.jpg',
    'images/canteen/item/0f450754-5b06-41e2-b693-729316c53f29.jpg',
    'images/canteen/item/131890ee-4b2e-407a-b4a6-c411e0000cbb.jpg',
    'images/canteen/item/14f10dad-7b8e-4ef2-88b3-3bfd7251b744.png',
    'images/canteen/item/18d6b3ab-bcc7-48cd-ae31-c4b15f8ba73a.jpg',
    'images/canteen/item/1eddd8a5-ca5f-4008-a71f-50fe56d48cb1.jpg',
    'images/canteen/item/1fae5f10-0de2-4c15-b8d9-1e16906245b0.png',
    'images/canteen/item/212dd3ad-877c-4577-93c3-ee01aeaf12fa.jpg',
    'images/canteen/item/242c7332-6000-420e-b78a-c1466ad401a5.png',
    'images/canteen/item/26557237-ae4b-4461-863c-ce68fdbebb19.jpg',
    'images/canteen/item/26826b4d-7c78-4c05-9572-78860caa3624.png',
    'images/canteen/item/2b09c5dc-24b2-4cb5-ab5b-d4e0e8d18568.png',
    'images/canteen/item/2bc92f9b-6462-4469-8cbc-18a85b35101f.png',
    'images/canteen/item/2f9ce6ac-086f-4942-b08a-9407725e4b46.jpg',
    'images/canteen/item/34af31f5-b569-41c0-acac-fe1b38a77b10.jpg',
    'images/canteen/item/36406e99-5368-4125-9689-968c5fee01b8.png',
    'images/canteen/item/36d799ae-5bb7-49bd-aeba-e69ecd8eb005.png',
    'images/canteen/item/3ab69a3a-cace-4121-bc97-ad41489d2294.png',
    'images/canteen/item/3d0c4926-7080-4d09-ac4b-c909bbeacde1.png',
    'images/canteen/item/3e0d0294-4a59-4670-98bb-84f299f55ae6.png',
    'images/canteen/item/40560bfd-a042-4179-bccf-488dfbcc9dc7.jpg',
    'images/canteen/item/40993a81-623b-4288-b352-051d0c5542e5.jpg',
    'images/canteen/item/417c67fd-dce8-4090-a56c-479062aef07d.jpg',
    'images/canteen/item/429436a0-7dc0-42a1-a9c0-d4298d842ebf.jpg',
    'images/canteen/item/43563bc3-c050-4553-8288-71cf5d7ba4bb.jpeg',
    'images/canteen/item/438ef512-a93f-4068-927d-0b0003245d52.png',
    'images/canteen/item/440bb6c9-c0ab-4a99-8118-13d62ee33af6.png',
    'images/canteen/item/4eacbeec-fae9-4913-b840-ad7bf4b2e69b.png',
    'images/canteen/item/571ab194-fb6c-498f-9746-2b8be748f3a2.jpg',
    'images/canteen/item/58314a9a-519f-4206-88e4-3015f7ddb6e0.jpg',
    'images/canteen/item/5870237c-fb99-4b5f-93ac-9cb2d7cdab6e.png',
    'images/canteen/item/5e08d8ad-2faa-4987-8e99-7a052c504ecd.jpg',
    'images/canteen/item/5ebdbce3-a820-4def-9763-7ba7960b86ec.jpg',
]

# (category_name, sku, item_name, price, cost)
# 6 All-Day Meals + 6 Pastries + 6 Hot Coffee + 6 Iced Coffee + 6 Non-Coffee + 5 Desserts = 35
ITEMS = [
    # ── All-Day Meals ─────────────────────────────────────────────
    ('All-Day Meals', 'FOOD-001', 'Tapsilog',               120, 70),
    ('All-Day Meals', 'FOOD-002', 'Longsilog',              110, 65),
    ('All-Day Meals', 'FOOD-003', 'Tocilog',                110, 65),
    ('All-Day Meals', 'FOOD-004', 'Chicksilog',             130, 75),
    ('All-Day Meals', 'FOOD-005', 'Beef Salpicao Rice',     160, 95),
    ('All-Day Meals', 'FOOD-006', 'Spam Silog',             140, 85),

    # ── Pastries ──────────────────────────────────────────────────
    ('Pastries', 'PAST-001', 'Ensaymada',                    55, 30),
    ('Pastries', 'PAST-002', 'Pandesal',                     15,  8),
    ('Pastries', 'PAST-003', 'Cheese Roll',                  60, 35),
    ('Pastries', 'PAST-004', 'Ham & Cheese Panini',          120, 70),
    ('Pastries', 'PAST-005', 'Butter Croissant',             85, 50),
    ('Pastries', 'PAST-006', 'Chicken Sandwich',             130, 75),

    # ── Hot Coffee ────────────────────────────────────────────────
    ('Hot Coffee', 'COF-001', 'Brewed Coffee',               80, 35),
    ('Hot Coffee', 'COF-002', 'Americano',                   95, 40),
    ('Hot Coffee', 'COF-003', 'Cappuccino',                  120, 55),
    ('Hot Coffee', 'COF-004', 'Cafe Latte',                  130, 60),
    ('Hot Coffee', 'COF-005', 'Spanish Latte',               150, 70),
    ('Hot Coffee', 'COF-006', 'Caramel Macchiato',           155, 72),

    # ── Iced Coffee ───────────────────────────────────────────────
    ('Iced Coffee', 'COF-007', 'Iced Americano',             110, 50),
    ('Iced Coffee', 'COF-008', 'Iced Latte',                 130, 60),
    ('Iced Coffee', 'COF-009', 'Iced Mocha',                 145, 68),
    ('Iced Coffee', 'COF-010', 'Iced Caramel Latte',         150, 70),
    ('Iced Coffee', 'COF-011', 'Iced Spanish Latte',         150, 70),
    ('Iced Coffee', 'COF-012', 'Iced Dirty Matcha',          160, 75),

    # ── Non-Coffee ────────────────────────────────────────────────
    ('Non-Coffee', 'DRK-001', 'Hot Chocolate',               110, 50),
    ('Non-Coffee', 'DRK-002', 'Matcha Latte',                140, 65),
    ('Non-Coffee', 'DRK-003', 'Taro Milk Tea',               130, 60),
    ('Non-Coffee', 'DRK-004', 'Strawberry Milk',             110, 50),
    ('Non-Coffee', 'DRK-005', 'Mango Shake',                 120, 55),
    ('Non-Coffee', 'DRK-006', 'Lemon Iced Tea',               90, 40),

    # ── Desserts (5) ──────────────────────────────────────────────
    ('Desserts', 'DES-001', 'Leche Flan',                     85, 45),
    ('Desserts', 'DES-002', 'Buko Pandan Salad',              90, 48),
    ('Desserts', 'DES-003', 'Chocolate Lava Cake',            130, 65),
    ('Desserts', 'DES-004', 'Cheesecake Slice',               140, 70),
    ('Desserts', 'DES-005', 'Halo-Halo',                      120, 58),
]


class Command(BaseCommand):
    help = 'Seed demo data for Tarsier Demo Cafe (idempotent)'

    def handle(self, *args, **options):
        self.seed_business_profile()
        categories = self.seed_categories()
        self.seed_items(categories)
        self.stdout.write(self.style.SUCCESS('Done.'))

    def seed_business_profile(self):
        profile, created = BusinessProfile.objects.get_or_create(id=1)
        profile.business_name = 'Tarsier Demo Cafe'
        profile.tagline = 'Your neighborhood coffee spot in Malolos'
        profile.address = 'Malolos, Bulacan, Philippines'
        profile.contact_number = '0917-123-4567'
        profile.email = 'hello@tarsiercafe.ph'
        profile.tin = '123-456-789-000'
        profile.receipt_header = 'Welcome to Tarsier Demo Cafe!'
        profile.receipt_footer = 'Thank you! See you again!'
        profile.save()
        action = 'Created' if created else 'Updated'
        self.stdout.write(f'  {action} business profile: {profile.business_name}')

    def seed_categories(self):
        category_data = [
            ('All-Day Meals', '🍽️', 'Rice meals and viands served all day'),
            ('Pastries',      '🥐', 'Fresh-baked bread, pastries, and sandwiches'),
            ('Hot Coffee',    '☕', 'Espresso-based and brewed hot coffee'),
            ('Iced Coffee',   '🧋', 'Cold and blended coffee drinks'),
            ('Non-Coffee',    '🍵', 'Tea, chocolate, fruit drinks, and more'),
            ('Desserts',      '🍮', 'Sweet treats and desserts'),
        ]
        categories = {}
        for name, emoji, desc in category_data:
            cat, created = ItemCategory.objects.get_or_create(
                name=name,
                defaults={'emoji': emoji, 'description': desc, 'is_active': True},
            )
            categories[name] = cat
            self.stdout.write(f'  Category {"created" if created else "exists"}: {name}')
        return categories

    def seed_items(self, categories):
        created_count = 0
        for idx, (cat_name, sku, name, price, cost) in enumerate(ITEMS):
            photo = IMGS[idx % len(IMGS)]
            item, created = Item.objects.get_or_create(
                sku=sku,
                defaults={
                    'category': categories[cat_name],
                    'name': name,
                    'price': price,
                    'purchase_price': cost,
                    'stock': 100,
                    'low_stock_threshold': 10,
                    'photo': photo,
                    'is_active': True,
                },
            )
            if created:
                created_count += 1
            status = 'created' if created else 'exists'
            self.stdout.write(f'  Item {status}: {sku} – {name}')
        self.stdout.write(f'  Items created this run: {created_count}')
