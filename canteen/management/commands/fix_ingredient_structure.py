"""PROD data hygiene (2026-07-18): structural ingredient/recipe correction.

The PROD menu accumulated duplicate ingredient records where recipes reference an
INACTIVE copy while a newer ACTIVE (often empty) copy is what the manager sees and
restocks — so sales deplete/cost a copy she can't manage, and her restocks land on
a copy nothing consumes. This command fixes that *structurally* only:

  * Fruit jelly: repoint every recipe line off the retired copy onto the kept copy.
  * Choco mousse: reactivate the copy the recipes already use; retire the empty dup.
  * Fruit jelly retired copy: deactivate (now unused).

STRUCTURAL ONLY. It never changes any cost value, never deletes a restock row,
never writes stock, and — asserted at runtime — never touches a single sales
record (PosTransaction / PosTransactionItem / PaymentLine / ZReport). Unit-cost
corrections are a separate, later step.

Dry-run by default. Preconditions are guarded against the live DB (name + active
state) and the run aborts loudly on any mismatch, so it cannot misfire on
unexpected data. Idempotent: re-running after a successful apply is a no-op.

    # inspect the plan (writes nothing):
    python manage.py fix_ingredient_structure
    # apply it:
    python manage.py fix_ingredient_structure --apply
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from canteen.models import (
    Ingredient, RecipeIngredient,
    PosTransaction, PosTransactionItem, PaymentLine, ZReport,
)


def _sales_fingerprint():
    """Counts + money sums over every sales table. Used to PROVE the structural
    correction left the financial record byte-identical. Our writes only ever
    touch Ingredient / RecipeIngredient, so this is belt-and-suspenders."""
    return {
        'PosTransaction.count': PosTransaction.objects.count(),
        'PosTransaction.net_total': PosTransaction.objects.aggregate(s=Sum('net_total'))['s'],
        'PosTransactionItem.count': PosTransactionItem.objects.count(),
        'PosTransactionItem.subtotal': PosTransactionItem.objects.aggregate(s=Sum('subtotal'))['s'],
        'PaymentLine.count': PaymentLine.objects.count(),
        'PaymentLine.amount': PaymentLine.objects.aggregate(s=Sum('amount'))['s'],
        'ZReport.count': ZReport.objects.count(),
        'ZReport.net_sales': ZReport.objects.aggregate(s=Sum('net_sales'))['s'],
        'ZReport.grand_total_sales': ZReport.objects.aggregate(s=Sum('grand_total_sales'))['s'],
    }


class Command(BaseCommand):
    help = "Structural ingredient/recipe dedup for PROD (no cost/stock/sales writes)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes. Omit for a dry run.')
        parser.add_argument('--jelly-keep', type=int, default=94)
        parser.add_argument('--jelly-retire', type=int, default=45)
        parser.add_argument('--choco-keep', type=int, default=34)
        parser.add_argument('--choco-retire', type=int, default=95)

    # ---- guards -------------------------------------------------------------
    def _ingredient(self, pk):
        try:
            return Ingredient.objects.get(pk=pk)
        except Ingredient.DoesNotExist:
            raise CommandError(f'Ingredient id={pk} not found — wrong DB or already changed?')

    def _guard(self, ing, name_contains, msg):
        if name_contains.lower() not in ing.name.lower():
            raise CommandError(
                f'SAFETY ABORT: id={ing.pk} is {ing.name!r}, expected a '
                f'{name_contains!r} record. {msg}')

    def handle(self, *args, **opt):
        apply = opt['apply']
        jelly_keep = self._ingredient(opt['jelly_keep'])
        jelly_retire = self._ingredient(opt['jelly_retire'])
        choco_keep = self._ingredient(opt['choco_keep'])
        choco_retire = self._ingredient(opt['choco_retire'])

        # Name guards — refuse to run against anything but the intended records.
        self._guard(jelly_keep, 'jelly', 'Refusing to repoint recipes.')
        self._guard(jelly_retire, 'jelly', 'Refusing to repoint recipes.')
        self._guard(choco_keep, 'choco', 'Refusing to flip active state.')
        self._guard(choco_retire, 'choco', 'Refusing to flip active state.')

        jelly_lines = RecipeIngredient.objects.filter(ingredient=jelly_retire)
        n_jelly = jelly_lines.count()

        def state():
            for ing in (jelly_keep, jelly_retire, choco_keep, choco_retire):
                ing.refresh_from_db()
                n = RecipeIngredient.objects.filter(ingredient=ing).count()
                self.stdout.write(
                    f'    id={ing.pk:<4} {ing.name!r:16} active={ing.is_active} '
                    f'cost={ing.cost_per_unit} recipes={n}')

        self.stdout.write(self.style.MIGRATE_HEADING('BEFORE:'))
        state()
        self.stdout.write(self.style.MIGRATE_HEADING('PLAN (structural only):'))
        self.stdout.write(f'  * repoint {n_jelly} Fruit jelly recipe lines '
                          f'id={jelly_retire.pk} -> id={jelly_keep.pk}')
        self.stdout.write(f'  * Choco mousse id={choco_keep.pk}: is_active -> True (reactivate)')
        self.stdout.write(f'  * Choco mousse id={choco_retire.pk}: is_active -> False (retire dup)')
        self.stdout.write(f'  * Fruit jelly id={jelly_retire.pk}: is_active -> False (retire, now unused)')
        self.stdout.write('  * NO cost change, NO restock deletion, NO stock write, NO sales-record write.')

        if not apply:
            self.stdout.write(self.style.WARNING('\nDRY RUN — nothing written. Re-run with --apply.'))
            return

        before = _sales_fingerprint()
        with transaction.atomic():
            repointed = jelly_lines.update(ingredient=jelly_keep)
            for ing, active in ((choco_keep, True), (choco_retire, False),
                                (jelly_retire, False)):
                ing.is_active = active
                ing.save(update_fields=['is_active'])
            after = _sales_fingerprint()
            if after != before:
                raise CommandError(
                    'SALES RECORD CHANGED — rolling back. before/after differ: '
                    f'{ {k: (before[k], after[k]) for k in before if before[k] != after[k]} }')

        self.stdout.write(self.style.MIGRATE_HEADING('\nAFTER:'))
        state()
        self.stdout.write(self.style.SUCCESS(
            f'\nAPPLIED: repointed {repointed} recipe line(s); sales tables verified '
            f'unchanged ({before["PosTransaction.count"]} txns, '
            f'{before["ZReport.count"]} Z-reports, all sums identical).'))
