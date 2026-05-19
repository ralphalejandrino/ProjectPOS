"""FEATURE-011-D: snapshot currency on ZReport.

ZReports are 10-year retained immutable records. Reading
BusinessProfile.currency at print time is a latent footgun if the
currency code ever changes, so it is frozen onto the row at finalize.

Backfill: existing rows (zero in practice today, but forward-safe) take
the current BusinessProfile.currency. Reverse is a no-op.
"""

from django.db import migrations, models


def backfill_currency(apps, schema_editor):
    ZReport = apps.get_model('canteen', 'ZReport')
    BusinessProfile = apps.get_model('canteen', 'BusinessProfile')
    bp = BusinessProfile.objects.first()
    code = (bp.currency if bp and bp.currency else 'PHP')
    ZReport.objects.update(currency=code)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0027_zreport_core'),
    ]

    operations = [
        migrations.AddField(
            model_name='zreport',
            name='currency',
            field=models.CharField(default='PHP', max_length=8),
        ),
        migrations.RunPython(backfill_currency, noop),
    ]
