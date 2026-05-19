"""ISSUE-105: unofficial Z mode (pre-BIR-accreditation support).

Adds ZReport.is_official. False means the Z was finalized without a
BusinessProfile MIN and must not be submitted to BIR. Reversible; no
data migration needed (no ZReports exist on prod yet).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0028_zreport_currency'),
    ]

    operations = [
        migrations.AddField(
            model_name='zreport',
            name='is_official',
            field=models.BooleanField(default=False),
        ),
    ]
