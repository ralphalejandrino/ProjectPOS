# Migration 0012: Add OfficialReceiptCounter for sequential per-day OR numbers.
# Format: OR-YYYYMMDD-XXXX (resets daily PHT, atomic via select_for_update).
# Existing transaction_no values are untouched — only new transactions use OR- format.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0011_variant_system_rework'),
    ]

    operations = [
        migrations.CreateModel(
            name='OfficialReceiptCounter',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(unique=True)),
                ('counter', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'indexes': [],
            },
        ),
        migrations.AddIndex(
            model_name='officialreceiptcounter',
            index=models.Index(fields=['date'], name='canteen_off_date_idx'),
        ),
    ]
