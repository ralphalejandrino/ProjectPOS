# Migration 0010: Add ItemVariant model and variant fields to PosTransactionItem
# Part of FEATURE-002 — variant system for per-item size/flavor/option pricing.

import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0009_restore_discount_and_shift_fields'),
    ]

    operations = [
        # 1. Create the ItemVariant table
        migrations.CreateModel(
            name='ItemVariant',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100, verbose_name='Variant Name')),
                ('price', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Variant Price')),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0, verbose_name='Display Order')),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='variants',
                    to='canteen.item',
                    verbose_name='Item',
                )),
            ],
            options={
                'ordering': ['sort_order', 'name'],
                'unique_together': {('item', 'name')},
            },
        ),

        # 2. Add variant FK to PosTransactionItem
        migrations.AddField(
            model_name='postransactionitem',
            name='variant',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='transaction_items',
                to='canteen.itemvariant',
                verbose_name='Variant',
            ),
        ),

        # 3. Add variant_name snapshot column to PosTransactionItem
        migrations.AddField(
            model_name='postransactionitem',
            name='variant_name',
            field=models.CharField(
                blank=True,
                default='',
                max_length=100,
                verbose_name='Variant Name (snapshot)',
            ),
        ),
    ]
