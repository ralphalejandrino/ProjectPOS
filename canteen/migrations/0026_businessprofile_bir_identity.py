"""FEATURE-011-B: BusinessProfile BIR identity fields.

Adds five BIR machine/accreditation identity fields to BusinessProfile.
All are blank-defaulted (the DateField is nullable) so existing installs
keep validating and saving without intervention. AddField is reversible.

Depends on 0024 (latest applied canteen migration). A separate
pre-existing currency-default drift (would be 0025) is intentionally not
addressed here — out of FEATURE-011-B scope.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0024_backfill_frozen_totals'),
    ]

    operations = [
        migrations.AddField(
            model_name='businessprofile',
            name='machine_identification_number',
            field=models.CharField(
                max_length=64, blank=True, default='',
                verbose_name='Machine Identification Number (MIN)',
                help_text='BIR-issued Machine Identification Number',
            ),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='machine_serial_number',
            field=models.CharField(
                max_length=64, blank=True, default='',
                verbose_name='Machine Serial Number',
            ),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='pos_accreditation_number',
            field=models.CharField(
                max_length=64, blank=True, default='',
                verbose_name='POS Accreditation Number',
            ),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='pos_permit_number',
            field=models.CharField(
                max_length=64, blank=True, default='',
                verbose_name='POS Permit Number',
            ),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='pos_accreditation_valid_until',
            field=models.DateField(
                null=True, blank=True,
                verbose_name='POS Accreditation Valid Until',
            ),
        ),
    ]
