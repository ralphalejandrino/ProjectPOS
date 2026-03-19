# Restores fields removed by 0008_remove_postransaction_canteen_pos_status_idx_and_more
# 0008 was a premature schema simplification that broke the discount system, shift tracking,
# void timestamp, and all related reports. This migration restores the production schema.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0008_remove_postransaction_canteen_pos_status_idx_and_more'),
    ]

    operations = [
        # --- PosTransaction: restore discount fields ---
        migrations.AddField(
            model_name='postransaction',
            name='discount_amount',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Discount applied to this transaction',
                max_digits=10,
            ),
        ),
        migrations.AddField(
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
        migrations.AddField(
            model_name='postransaction',
            name='discount_id_number',
            field=models.CharField(
                blank=True,
                default='',
                help_text='SC/PWD ID number for audit trail',
                max_length=50,
            ),
        ),
        # --- PosTransaction: restore shift FK ---
        migrations.AddField(
            model_name='postransaction',
            name='shift',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='transactions',
                to='canteen.shift',
            ),
        ),
        # --- PosTransaction: restore voided_at ---
        migrations.AddField(
            model_name='postransaction',
            name='voided_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # --- PosTransaction: restore indexes ---
        migrations.AddIndex(
            model_name='postransaction',
            index=models.Index(fields=['status'], name='canteen_pos_status_idx'),
        ),
        migrations.AddIndex(
            model_name='postransaction',
            index=models.Index(fields=['shift'], name='canteen_pos_shift_idx'),
        ),
        # --- BusinessProfile: restore per-type discount config ---
        migrations.AddField(
            model_name='businessprofile',
            name='sc_discount_enabled',
            field=models.BooleanField(default=True, verbose_name='SC Discount Enabled'),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='sc_discount_rate',
            field=models.DecimalField(
                decimal_places=2,
                default=20.0,
                max_digits=5,
                verbose_name='SC Discount Rate (%)',
            ),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='pwd_discount_enabled',
            field=models.BooleanField(default=True, verbose_name='PWD Discount Enabled'),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='pwd_discount_rate',
            field=models.DecimalField(
                decimal_places=2,
                default=20.0,
                max_digits=5,
                verbose_name='PWD Discount Rate (%)',
            ),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='promo_discount_enabled',
            field=models.BooleanField(default=False, verbose_name='Promo Discount Enabled'),
        ),
    ]
