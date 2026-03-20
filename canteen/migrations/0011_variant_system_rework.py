# Migration 0011: Replace flat ItemVariant with shared VariantGroup system.
# Part of FEATURE-002-v2 — drop 0010 schema (zero rows confirmed), create
# VariantGroup, VariantOption, CategoryVariantGroup, ProductVariantGroup,
# TransactionItemVariant; add base_price + final_price to PosTransactionItem.

import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0010_item_variant'),
    ]

    operations = [

        # ----------------------------------------------------------------
        # PART 1 — Undo migration 0010
        # Order: remove FKs from PosTransactionItem first, then delete model.
        # ----------------------------------------------------------------

        # 1a. Drop variant FK from PosTransactionItem
        migrations.RemoveField(
            model_name='postransactionitem',
            name='variant',
        ),

        # 1b. Drop variant_name snapshot from PosTransactionItem
        migrations.RemoveField(
            model_name='postransactionitem',
            name='variant_name',
        ),

        # 1c. Delete the ItemVariant model (and its table)
        migrations.DeleteModel(
            name='ItemVariant',
        ),

        # ----------------------------------------------------------------
        # PART 2 — Create new schema
        # ----------------------------------------------------------------

        # 2a. VariantGroup — reusable named group (e.g. "Beverage Sizes")
        migrations.CreateModel(
            name='VariantGroup',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('selection_type', models.CharField(
                    max_length=10,
                    choices=[('single', 'Single'), ('multi', 'Multi')],
                    default='single',
                )),
                ('is_required', models.BooleanField(default=False)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['sort_order', 'name'],
            },
        ),

        # 2b. VariantOption — individual choice within a group
        migrations.CreateModel(
            name='VariantOption',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('group', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='options',
                    to='canteen.variantgroup',
                )),
                ('name', models.CharField(max_length=100)),
                ('price_modifier', models.DecimalField(max_digits=8, decimal_places=2, default=0)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['sort_order', 'name'],
            },
        ),

        # 2c. CategoryVariantGroup — M2M through: assign groups to categories
        migrations.CreateModel(
            name='CategoryVariantGroup',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('category', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='variant_groups',
                    to='canteen.itemcategory',
                )),
                ('group', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='category_assignments',
                    to='canteen.variantgroup',
                )),
                ('is_required_override', models.BooleanField(null=True, blank=True)),
            ],
        ),
        migrations.AlterUniqueTogether(
            name='categoryvariantgroup',
            unique_together={('category', 'group')},
        ),

        # 2d. ProductVariantGroup — per-product override (enable/disable/require a group)
        migrations.CreateModel(
            name='ProductVariantGroup',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='variant_group_overrides',
                    to='canteen.item',
                )),
                ('group', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='product_overrides',
                    to='canteen.variantgroup',
                )),
                ('enabled', models.BooleanField(default=True)),
                ('is_required_override', models.BooleanField(null=True, blank=True)),
            ],
        ),
        migrations.AlterUniqueTogether(
            name='productvariantgroup',
            unique_together={('product', 'group')},
        ),

        # 2e. TransactionItemVariant — snapshot of each selected option per line item
        migrations.CreateModel(
            name='TransactionItemVariant',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('transaction_item', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='variant_selections',
                    to='canteen.postransactionitem',
                )),
                ('group_name', models.CharField(max_length=100)),
                ('option_name', models.CharField(max_length=100)),
                ('price_modifier', models.DecimalField(max_digits=8, decimal_places=2)),
            ],
        ),

        # 2f. Add base_price to PosTransactionItem (null for historical rows)
        migrations.AddField(
            model_name='postransactionitem',
            name='base_price',
            field=models.DecimalField(
                max_digits=8,
                decimal_places=2,
                null=True,
                blank=True,
            ),
        ),

        # 2g. Add final_price to PosTransactionItem (null for historical rows)
        migrations.AddField(
            model_name='postransactionitem',
            name='final_price',
            field=models.DecimalField(
                max_digits=8,
                decimal_places=2,
                null=True,
                blank=True,
            ),
        ),
    ]
