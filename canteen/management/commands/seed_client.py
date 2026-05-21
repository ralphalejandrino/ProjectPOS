"""
seed_client — reusable fresh-install seeder for a TarsierPOS client box.

Initializes a FRESH client install with the bare minimum to start trading:
  * one BusinessProfile in UNOFFICIAL mode (all BIR fields blank — decision #31)
  * a clean OR series (next receipt of the day starts at 0001)
  * one admin account + N cashier accounts, each with a random numeric PIN

It creates NO demo categories, items, or transactions.

Usage:
    python manage.py seed_client --business-name "PROD Cafe" --or-prefix "OR-PROD"
    python manage.py seed_client --business-name "PROD Cafe" --or-prefix "OR-PROD" \
        --cashiers 3 --admin-user manager --force

By default it REFUSES to run if a BusinessProfile already exists (guards a live
box). Pass --force to re-seed anyway.

OR-prefix note: the current OR generator (PosTransaction._generate_or_number)
emits the fixed format ``OR-YYYYMMDD-NNNN`` and has no configurable-prefix
field on any model. Wiring a custom prefix would require a schema migration,
which is out of scope for this seed command. The supplied --or-prefix is
therefore validated, recorded in the handoff output and the DEPLOY-003 receipt
for the client record, and the OR counter is reset so numbering starts at 0001;
it does not (yet) change the literal characters in generated OR numbers.

Generated PINs are printed to stdout ONCE and are never written to any file.
"""
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from canteen.models import BusinessProfile, OfficialReceiptCounter

User = get_user_model()


def _generate_pin():
    """Random 4-6 digit numeric PIN (leading zeros allowed). Stored hashed via
    User.set_password — same mechanism the kiosk quick-login authenticates against."""
    length = secrets.choice((4, 5, 6))
    return ''.join(secrets.choice('0123456789') for _ in range(length))


class Command(BaseCommand):
    help = 'Seed a fresh client install: unofficial BusinessProfile, OR series, and accounts. No demo data.'

    def add_arguments(self, parser):
        parser.add_argument('--business-name', required=True,
                            help='Business name, e.g. "PROD Cafe" (sets BusinessProfile.business_name).')
        parser.add_argument('--or-prefix', required=True,
                            help='OR series prefix, e.g. "OR-PROD" (recorded for the client; see OR-prefix note).')
        parser.add_argument('--cashiers', type=int, default=2,
                            help='Number of cashier accounts to create (default 2).')
        parser.add_argument('--admin-user', default='admin',
                            help='Username for the single admin account (default "admin").')
        parser.add_argument('--force', action='store_true', default=False,
                            help='Allow running even if a BusinessProfile already exists.')

    def handle(self, *args, **options):
        business_name = options['business_name'].strip()
        or_prefix = options['or_prefix'].strip()
        n_cashiers = options['cashiers']
        admin_user = options['admin_user'].strip()
        force = options['force']

        if not business_name:
            raise CommandError('--business-name must not be blank.')
        if not or_prefix:
            raise CommandError('--or-prefix must not be blank.')
        if n_cashiers < 0:
            raise CommandError('--cashiers must be 0 or greater.')

        if BusinessProfile.objects.exists() and not force:
            raise CommandError(
                'A BusinessProfile already exists — refusing to seed (this guards a live box). '
                'Pass --force to re-seed anyway.'
            )

        credentials = []

        with transaction.atomic():
            # 1. BusinessProfile — unofficial mode (all BIR fields blank).
            profile = BusinessProfile.objects.first() or BusinessProfile()
            profile.business_name = business_name
            profile.address = ''
            profile.currency = 'PHP'
            # ALL BIR identity fields blank → Z finalize resolves is_official=False.
            profile.tin = ''
            profile.machine_identification_number = ''
            profile.machine_serial_number = ''
            profile.pos_accreditation_number = ''
            profile.pos_permit_number = ''
            profile.pos_accreditation_valid_until = None
            profile.save()

            # 2. OR series — clear per-day counters so the next receipt is 0001.
            OfficialReceiptCounter.objects.all().delete()

            # 3. Accounts — 1 admin + N cashiers, each with a random numeric PIN.
            admin_pin = _generate_pin()
            admin, _ = User.objects.get_or_create(
                username=admin_user, defaults={'is_active': True},
            )
            admin.role = 'admin'
            admin.is_active = True
            admin.set_password(admin_pin)
            admin.save()
            credentials.append((admin_user, 'admin', admin_pin))

            for i in range(1, n_cashiers + 1):
                username = f'cashier{i}'
                pin = _generate_pin()
                user, _ = User.objects.get_or_create(
                    username=username, defaults={'is_active': True},
                )
                user.role = 'cashier'
                user.is_active = True
                user.set_password(pin)
                user.save()
                credentials.append((username, 'cashier', pin))

        # ── Summary ──────────────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS('Client seed complete.'))
        self.stdout.write(f'  Business name : {business_name}')
        self.stdout.write(f'  Mode          : UNOFFICIAL (BIR fields blank — decision #31)')
        self.stdout.write(f'  OR prefix     : {or_prefix}  (counter reset → next OR starts at 0001)')
        self.stdout.write(f'  Accounts      : 1 admin + {n_cashiers} cashier(s)')

        self.stdout.write('')
        self.stdout.write('=' * 60)
        self.stdout.write('HAND OFF THESE CREDENTIALS — not stored in git')
        self.stdout.write('=' * 60)
        for username, role, pin in credentials:
            self.stdout.write(f'  {role:<8} {username:<16} PIN: {pin}')
        self.stdout.write('=' * 60)
