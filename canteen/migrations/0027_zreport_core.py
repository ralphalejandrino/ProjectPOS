"""FEATURE-011-C: ZReport core schema.

Creates ZCounter (gapless Z numbering singleton) and the immutable
ZReport snapshot model. Depends on 0026; the pre-existing
total_amount_currency drift (which would be an orphan 0025) is
deliberately NOT bundled here — out of FEATURE-011-C scope, consistent
with Sessions A/B.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0026_businessprofile_bir_identity'),
    ]

    operations = [
        migrations.CreateModel(
            name='ZCounter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('z_counter', models.PositiveIntegerField(default=0)),
                ('reset_counter', models.PositiveIntegerField(default=0)),
                ('grand_total', models.DecimalField(decimal_places=2, default=0, max_digits=16)),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(('pk', 1)), name='zcounter_singleton')],
            },
        ),
        migrations.CreateModel(
            name='ZReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('z_counter', models.PositiveIntegerField(unique=True)),
                ('reset_counter', models.PositiveIntegerField(default=0)),
                ('business_date', models.DateField()),
                ('started_at', models.DateTimeField()),
                ('finalized_at', models.DateTimeField(auto_now_add=True)),
                ('business_name', models.CharField(max_length=255)),
                ('business_tin', models.CharField(blank=True, default='', max_length=64)),
                ('business_address', models.CharField(blank=True, default='', max_length=512)),
                ('machine_identification_number', models.CharField(blank=True, default='', max_length=64)),
                ('machine_serial_number', models.CharField(blank=True, default='', max_length=64)),
                ('pos_accreditation_number', models.CharField(blank=True, default='', max_length=64)),
                ('pos_permit_number', models.CharField(blank=True, default='', max_length=64)),
                ('first_or_number', models.CharField(blank=True, default='', max_length=32)),
                ('last_or_number', models.CharField(blank=True, default='', max_length=32)),
                ('voided_or_numbers', models.JSONField(default=list)),
                ('transaction_count', models.PositiveIntegerField(default=0)),
                ('voided_count', models.PositiveIntegerField(default=0)),
                ('gross_sales', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('discount_total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('net_sales', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('vatable_sales', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('vat_exempt_sales', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('zero_rated_sales', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('output_vat', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('sc_discount_total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('pwd_discount_total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('promo_discount_total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('payment_breakdown', models.JSONField(default=dict)),
                ('opening_cash', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('cash_collected', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('cash_expected', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('cash_counted', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('over_short', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('grand_total_sales', models.DecimalField(decimal_places=2, default=0, max_digits=16)),
                ('cashier', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='z_reports', to=settings.AUTH_USER_MODEL)),
                ('shift', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='z_report', to='canteen.shift')),
            ],
            options={
                'ordering': ['-z_counter'],
                'indexes': [models.Index(fields=['business_date'], name='canteen_zre_busines_e8d618_idx'), models.Index(fields=['finalized_at'], name='canteen_zre_finaliz_85294c_idx')],
            },
        ),
    ]
