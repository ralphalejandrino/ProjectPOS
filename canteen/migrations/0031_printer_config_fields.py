"""ISSUE-099: replace the IP-as-enable-flag printer workaround with an
explicit transport mode, and add paper-width / font calibration fields.

Schema:
    + printer_mode  (usb / network / disabled, default disabled)
    + paper_width   (58mm / 80mm, default 58mm)
    + printer_font  (A / B, default B)
    - printer_enabled (superseded by printer_mode)

Data step (Decision 2 = option (c) — infer mode from the legacy printer_ip):
    printer_ip == ''/None  -> 'disabled'
    printer_ip == 127.0.0.1 -> 'usb'   (the documented USB workaround marker)
    otherwise               -> 'network'

paper_width and printer_font keep their defaults (58mm / Font B), matching
the current FIX-PENDING-14 calibration. printer_enabled is dropped AFTER the
data step so historical intent is recoverable from printer_ip alone.

Reverse: re-add printer_enabled (default False) and drop the new fields.
"""

from django.db import migrations, models


def set_printer_mode(apps, schema_editor):
    BusinessProfile = apps.get_model('canteen', 'BusinessProfile')
    for profile in BusinessProfile.objects.all():
        if not profile.printer_ip:
            profile.printer_mode = 'disabled'
        elif profile.printer_ip == '127.0.0.1':
            profile.printer_mode = 'usb'
        else:
            profile.printer_mode = 'network'
        profile.save(update_fields=['printer_mode'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0030_one_open_shift_per_cashier'),
    ]

    operations = [
        migrations.AddField(
            model_name='businessprofile',
            name='printer_mode',
            field=models.CharField(
                choices=[('usb', 'USB'), ('network', 'Network'), ('disabled', 'Disabled')],
                default='disabled', max_length=10, verbose_name='Printer Mode',
            ),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='paper_width',
            field=models.CharField(
                choices=[('58mm', '58mm'), ('80mm', '80mm')],
                default='58mm', max_length=10, verbose_name='Paper Width',
            ),
        ),
        migrations.AddField(
            model_name='businessprofile',
            name='printer_font',
            field=models.CharField(
                choices=[('A', 'Font A (wide)'), ('B', 'Font B (narrow)')],
                default='B', max_length=2, verbose_name='Printer Font',
            ),
        ),
        migrations.RunPython(set_printer_mode, noop_reverse),
        migrations.RemoveField(
            model_name='businessprofile',
            name='printer_enabled',
        ),
    ]
