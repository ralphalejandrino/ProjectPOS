# Generated manually — adds indexes and discount_type choices

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0006_postransaction_voided_at'),
    ]

    operations = [
        # Add status and shift indexes to PosTransaction
        migrations.AddIndex(
            model_name='postransaction',
            index=models.Index(fields=['status'], name='canteen_pos_status_idx'),
        ),
        migrations.AddIndex(
            model_name='postransaction',
            index=models.Index(fields=['shift'], name='canteen_pos_shift_idx'),
        ),
        # Add choices to discount_type
        migrations.AlterField(
            model_name='postransaction',
            name='discount_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'None'),
                    ('fixed', 'Fixed Amount'),
                    ('percentage', 'Percentage'),
                    ('sc', 'Senior Citizen (20%)'),
                    ('pwd', 'PWD (20%)'),
                    ('promo', 'Promo'),
                ],
                default='',
                help_text='Type of discount applied',
                max_length=20,
            ),
        ),
    ]
