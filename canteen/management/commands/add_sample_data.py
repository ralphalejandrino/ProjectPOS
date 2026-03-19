from django.core.management.base import BaseCommand
from canteen.models import ItemCategory, Item

class Command(BaseCommand):
    help = 'Add sample POS data for demo'

    def handle(self, *args, **kwargs):
        self.stdout.write('Creating sample data...')

        # Create categories
        food_cat, _ = ItemCategory.objects.get_or_create(name='Food')
        drinks_cat, _ = ItemCategory.objects.get_or_create(name='Drinks')
        snacks_cat, _ = ItemCategory.objects.get_or_create(name='Snacks')

        # Create items (using correct fields: bar_code, price, stock)
        items = [
            ('Fried Chicken', food_cat, 45.00, 100, '1001'),
            ('Pancit Canton', food_cat, 35.00, 80, '1002'),
            ('Rice Meal', food_cat, 50.00, 60, '1003'),
            ('Coca-Cola', drinks_cat, 20.00, 150, '2001'),
            ('Bottled Water', drinks_cat, 15.00, 200, '2002'),
            ('Orange Juice', drinks_cat, 25.00, 100, '2003'),
            ('Chippy', snacks_cat, 10.00, 120, '3001'),
            ('Piattos', snacks_cat, 12.00, 100, '3002'),
        ]

        for name, cat, price, stock, barcode in items:
            Item.objects.get_or_create(
                bar_code=barcode,
                defaults={
                    'name': name,
                    'category': cat,
                    'price': price,
                    'stock': stock,
                }
            )

        self.stdout.write(self.style.SUCCESS('✅ Sample data created!'))
        self.stdout.write(f'Categories: {ItemCategory.objects.count()}')
        self.stdout.write(f'Items: {Item.objects.count()}')
