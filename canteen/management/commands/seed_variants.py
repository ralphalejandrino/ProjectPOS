"""
Management command: seed_variants
Creates the default VariantGroups and VariantOptions for a Philippine canteen.
Safe to run multiple times — skips existing groups by name.
"""
from django.core.management.base import BaseCommand
from canteen.models import VariantGroup, VariantOption


SEED_DATA = [
    {
        'name': 'Size',
        'selection_type': 'single',
        'is_required': False,
        'sort_order': 1,
        'options': [
            {'name': 'Regular', 'price_modifier': '0.00', 'sort_order': 1},
            {'name': 'Large',   'price_modifier': '20.00', 'sort_order': 2},
        ],
    },
    {
        'name': 'Temperature',
        'selection_type': 'single',
        'is_required': False,
        'sort_order': 2,
        'options': [
            {'name': 'Hot',     'price_modifier': '0.00',  'sort_order': 1},
            {'name': 'Iced',    'price_modifier': '10.00', 'sort_order': 2},
            {'name': 'Blended', 'price_modifier': '15.00', 'sort_order': 3},
        ],
    },
    {
        'name': 'Sweetness',
        'selection_type': 'single',
        'is_required': False,
        'sort_order': 3,
        'options': [
            {'name': 'Less Sweet',   'price_modifier': '0.00', 'sort_order': 1},
            {'name': 'Regular',      'price_modifier': '0.00', 'sort_order': 2},
            {'name': 'Extra Sweet',  'price_modifier': '0.00', 'sort_order': 3},
        ],
    },
    {
        'name': 'Milk',
        'selection_type': 'single',
        'is_required': False,
        'sort_order': 4,
        'options': [
            {'name': 'Regular', 'price_modifier': '0.00',  'sort_order': 1},
            {'name': 'Oat',     'price_modifier': '15.00', 'sort_order': 2},
            {'name': 'Almond',  'price_modifier': '15.00', 'sort_order': 3},
            {'name': 'Soy',     'price_modifier': '15.00', 'sort_order': 4},
        ],
    },
    {
        'name': 'Additionals',
        'selection_type': 'multi',
        'is_required': False,
        'sort_order': 5,
        'options': [
            {'name': 'Extra Shot',    'price_modifier': '20.00', 'sort_order': 1},
            {'name': 'Whipped Cream', 'price_modifier': '15.00', 'sort_order': 2},
            {'name': 'Syrup',         'price_modifier': '10.00', 'sort_order': 3},
        ],
    },
    {
        'name': 'Spice Level',
        'selection_type': 'single',
        'is_required': False,
        'sort_order': 6,
        'options': [
            {'name': 'Mild',       'price_modifier': '0.00', 'sort_order': 1},
            {'name': 'Medium',     'price_modifier': '0.00', 'sort_order': 2},
            {'name': 'Hot',        'price_modifier': '0.00', 'sort_order': 3},
            {'name': 'Extra Hot',  'price_modifier': '0.00', 'sort_order': 4},
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed default VariantGroups and VariantOptions. Safe to re-run — skips existing.'

    def handle(self, *args, **options):
        created_groups = 0
        created_options = 0

        for group_data in SEED_DATA:
            options_data = group_data.pop('options')
            group, group_created = VariantGroup.objects.get_or_create(
                name=group_data['name'],
                defaults=group_data,
            )
            if group_created:
                created_groups += 1
                self.stdout.write(f"  Created group: {group.name}")
            else:
                self.stdout.write(f"  Skipped (exists): {group.name}")

            for opt_data in options_data:
                _, opt_created = VariantOption.objects.get_or_create(
                    group=group,
                    name=opt_data['name'],
                    defaults=opt_data,
                )
                if opt_created:
                    created_options += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created {created_groups} groups, {created_options} options."
        ))
