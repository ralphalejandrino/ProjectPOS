# FEATURE-040 follow-up: default printer_font to Font A (legible) instead of the
# narrow Font B. Only changes the field default for zero-config/new stores;
# existing rows are untouched (operators who chose B keep it).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0031_printer_config_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='businessprofile',
            name='printer_font',
            field=models.CharField(
                choices=[('A', 'Font A (wide)'), ('B', 'Font B (narrow)')],
                default='A', max_length=2, verbose_name='Printer Font',
            ),
        ),
    ]
