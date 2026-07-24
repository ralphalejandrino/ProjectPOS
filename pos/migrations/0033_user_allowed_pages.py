# FEATURE-044: per-user page-access override field.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pos', '0032_printer_font_default_a'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='allowed_pages',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
