# Generated for ISSUE-051: non-negative validators on Item.price and
# PosTransactionItem.quantity. Unrelated djmoney currency-choices drift
# detected by makemigrations is intentionally omitted from this migration
# to keep the change surgical.

import canteen.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0017_discount_type_none_default'),
    ]

    operations = [
        migrations.AlterField(
            model_name='item',
            name='price',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=10,
                validators=[canteen.validators.validate_non_negative_price],
                verbose_name='Selling Price',
            ),
        ),
        migrations.AlterField(
            model_name='postransactionitem',
            name='quantity',
            field=models.PositiveIntegerField(
                default=1,
                validators=[canteen.validators.validate_non_negative_quantity],
                verbose_name='Quantity',
            ),
        ),
    ]
