"""PROD data hygiene (2026-07-19): ingredient COSTING correction.

Companion to fix_ingredient_structure (which fixed the record structure). This
one fixes the cost values, from the manager's own answers (2026-07-19):

  * Fullcream milk: the Jul-8 restock was entered as "144 @ P76.5833 each";
    she actually bought 12 one-liter boxes at P76.58/box -> edit the entry to
    12000 ml @ P0.0766/ml (audited edit_restock; cost recomputes; the stock
    delta is fact — she DID receive 12 L — the physical count owns truth).
  * Choco mousse: void the phantom restock #38 (mis-tap; the caramel redo
    already exists as #39), then set the true cost P400/kg / 100 scoops =
    P4.00/scoop directly — no real choco purchase happened, so there is no
    restock row to carry the price and cost_before predates migration 0048.
  * Espresso Syrup: cost was typed at setup as P2.74/ml; her bottle is
    P350/960 ml -> P0.3646/ml. No restock exists — direct set.
  * Dabba Cup 12oz: config P420/100 pcs confirmed, never restocked -> P4.20.
  * Condensed: system said P50/200; the real can is P44/390 g = 300 ml
    (standard condensada can is labeled 300 ml / 390 g) -> P44, factor 300,
    P0.1467/ml.
  * Waffle choco syrup: no purchase config at all; P280/500 g bottle,
    mirroring waffle CARAMEL syrup's existing g->ml convention (factor 385)
    -> P280, factor 385, P0.7273/ml.
  * Blacktea recipe lines: Latte-Melon has NO tea (her words: melon powder +
    nata only) -> delete its two 100/150 lines; Red Velvet does have tea ->
    its 200 line becomes 0.05 pcs like the other 28 lines. The ingredient's
    own cost (P240/10 teabags = P24/bag) is confirmed correct and untouched.

NEVER touches a sales record — asserted at runtime with the same fingerprint
guard as fix_ingredient_structure. Every step is guarded by the expected
CURRENT value: already-in-target-state -> skipped (idempotent), anything else
-> loud abort, nothing written. Dry-run by default:

    python manage.py fix_ingredient_costing            # plan only
    python manage.py fix_ingredient_costing --apply
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from canteen.models import (
    Ingredient, IngredientRestockLog, Item, RecipeIngredient, User,
    PosTransaction, PosTransactionItem, PaymentLine, ZReport,
)
from canteen.services import edit_restock, void_restock


def _sales_fingerprint():
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


D = Decimal

# Direct cost/config sets: pk -> (name guard, field, expect current, target).
COST_SETS = [
    (19, 'Espresso Syrup',     'cost_per_unit',           D('2.74'), D('0.3646')),
    (67, 'Dabba Cup 12oz',     'cost_per_unit',           D('0'),    D('4.2000')),
    (51, 'Condensed',          'cost_per_unit',           D('0'),    D('0.1467')),
    (51, 'Condensed',          'last_purchase_price',     D('50'),   D('44')),
    (51, 'Condensed',          'purchase_to_base_factor', D('200'),  D('300')),
    (54, 'Waffle choco syrup', 'cost_per_unit',           D('0'),    D('0.7273')),
    (54, 'Waffle choco syrup', 'last_purchase_price',     None,      D('280')),
    (54, 'Waffle choco syrup', 'purchase_to_base_factor', None,      D('385')),
    # Choco mousse LAST: must follow the #38 void (void's recost is a no-op —
    # no surviving purchase, NULL cost_before — so this set is what lands P4).
    (34, 'Choco mousse',       'cost_per_unit',           D('0.8063'), D('4.0000')),
]

MILK_PK, MILK_RESTOCK_PK = 22, 6
CHOCO_RESTOCK_PK = 38


class Command(BaseCommand):
    help = "PROD one-time ingredient costing correction (manager-confirmed values)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes. Omit for a dry run.')
        parser.add_argument('--as-user', default='admin',
                            help='Username stamped on the audited corrections.')

    def _say(self, msg):
        self.stdout.write(msg)

    def handle(self, *args, **options):
        apply_ = options['apply']
        try:
            actor = User.objects.get(username=options['as_user'])
        except User.DoesNotExist:
            raise CommandError(f"--as-user {options['as_user']!r} not found")

        with transaction.atomic():
            before = _sales_fingerprint()
            skipped = applied = 0

            # 1. Fullcream milk restock #6: 144 @ 76.5833 -> 12000 ml @ 0.0766.
            milk = Ingredient.objects.select_for_update().get(pk=MILK_PK)
            self._guard(milk.name == 'Fullcream milk', f'pk {MILK_PK} is {milk.name!r}, not Fullcream milk')
            r6 = IngredientRestockLog.objects.get(pk=MILK_RESTOCK_PK, ingredient=milk)
            if r6.corrected_at is not None and r6.quantity_added == 12000:
                self._say('  ~ milk restock #6 already corrected — skip')
                skipped += 1
            else:
                self._guard(
                    not r6.is_voided and r6.corrected_at is None
                    and r6.quantity_added == 144 and r6.cost_per_unit == D('76.5833'),
                    f'milk restock #6 unexpected state: qty={r6.quantity_added} '
                    f'cost={r6.cost_per_unit} voided={r6.is_voided} corrected={r6.corrected_at}')
                res = edit_restock(
                    r6, quantity_added=12000, cost_per_unit=D('0.0766'), user=actor,
                    reason='mgr-confirmed 2026-07-19: 12 boxes x 1L @ P76.58/box '
                           '(was entered as 144 units @ box price each)')
                # The edit clears the package-price memory; we KNOW the real
                # package price, so restore it for the next restock's prefill.
                milk.refresh_from_db()
                milk.last_purchase_price = D('76.58')
                milk.save(update_fields=['last_purchase_price'])
                self._say(f'  * milk restock #6 -> 12000 ml @ 0.0766; ingredient cost '
                          f'now {milk.cost_per_unit}, stock {res["new_stock"]}')
                applied += 1

            # 2. Void phantom choco restock #38 (redo already exists as #39).
            r38 = IngredientRestockLog.objects.get(pk=CHOCO_RESTOCK_PK)
            self._guard(r38.ingredient_id == 34, f'#38 belongs to ingredient {r38.ingredient_id}, not 34')
            if r38.is_voided:
                self._say('  ~ phantom restock #38 already voided — skip')
                skipped += 1
            else:
                self._guard(
                    r38.quantity_added == 730 and r38.cost_per_unit == D('0.78'),
                    f'#38 unexpected: qty={r38.quantity_added} cost={r38.cost_per_unit}')
                res = void_restock(
                    r38, user=actor,
                    reason='mgr-confirmed mis-tap of the caramel purchase '
                           '(correct caramel entry #39 exists); phantom on choco')
                self._say(f'  * phantom #38 voided; choco stock {res["new_stock"]} '
                          f'(negative until the physical count — expected)')
                applied += 1

            # 3. Direct cost/config sets (setup-era values with no purchase
            #    history to correct through a restock row).
            for pk, name, field, expect, target in COST_SETS:
                ing = Ingredient.objects.select_for_update().get(pk=pk)
                self._guard(ing.name == name, f'pk {pk} is {ing.name!r}, not {name!r}')
                current = getattr(ing, field)
                if current == target:
                    self._say(f'  ~ {name}.{field} already {target} — skip')
                    skipped += 1
                    continue
                self._guard(current == expect,
                            f'{name}.{field} is {current!r}, expected {expect!r}')
                setattr(ing, field, target)
                ing.save(update_fields=[field])
                self._say(f'  * {name}.{field}: {expect} -> {target}')
                applied += 1

            # Waffle choco syrup: mirror waffle caramel's purchase unit (g).
            wcs = Ingredient.objects.get(pk=54)
            caramel = Ingredient.objects.get(pk=55, name='waffle caramel syrup')
            if wcs.purchase_unit_id is None and caramel.purchase_unit_id is not None:
                wcs.purchase_unit_id = caramel.purchase_unit_id
                wcs.save(update_fields=['purchase_unit'])
                self._say('  * Waffle choco syrup purchase unit <- waffle caramel\'s')
                applied += 1

            # 4. Blacktea recipe lines.
            melon = self._one_item('Latte - Melon')
            bogus = RecipeIngredient.objects.filter(item=melon, ingredient_id=49)
            if not bogus.exists():
                self._say('  ~ Latte-Melon blacktea lines already gone — skip')
                skipped += 1
            else:
                qtys = sorted(l.quantity_used for l in bogus)
                self._guard(qtys == [D('100'), D('150')],
                            f'Latte-Melon blacktea lines unexpected: {qtys}')
                bogus.delete()
                self._say('  * deleted 2 bogus Latte-Melon blacktea lines (no tea in it)')
                applied += 1

            # Red Velvet DOES have tea, at the house convention of 0.05 pcs
            # (1/20 of a teabag). The manager may have already added correct
            # per-variant lines herself, leaving the old mis-united line
            # orphaned alongside them — so a bogus line whose variant is
            # already covered must be DELETED, not edited down, or that
            # variant would carry two 0.05 lines and double-count the tea.
            rv = self._one_item('Milk Tea - Red Velvet')
            rv_lines = list(RecipeIngredient.objects.filter(item=rv, ingredient_id=49))
            self._guard(bool(rv_lines), 'Red Velvet has no blacktea line')
            good = [l for l in rv_lines if l.quantity_used == D('0.05')]
            bogus_rv = [l for l in rv_lines if l.quantity_used != D('0.05')]
            if not bogus_rv:
                self._say(f'  ~ Red Velvet blacktea already 0.05 ({len(good)} line(s)) — skip')
                skipped += 1
            else:
                qtys = sorted(l.quantity_used for l in bogus_rv)
                self._guard(all(q == D('200') for q in qtys),
                            f'Red Velvet blacktea unexpected qty {qtys}, expected 200')
                covered = {l.variant_id for l in good}
                for line in bogus_rv:
                    if line.variant_id in covered:
                        line.delete()
                        self._say('  * Red Velvet: deleted orphaned 200-pcs blacktea line '
                                  '(variant already covered at 0.05)')
                    else:
                        line.quantity_used = D('0.05')
                        line.save(update_fields=['quantity_used'])
                        covered.add(line.variant_id)
                        self._say('  * Red Velvet blacktea line: 200 -> 0.05 pcs (1/20 teabag)')
                    applied += 1

            after = _sales_fingerprint()
            if after != before:
                raise CommandError(f'SALES FINGERPRINT CHANGED — aborted. {before} != {after}')

            if not apply_:
                transaction.set_rollback(True)
                self._say(self.style.WARNING(
                    f'DRY RUN — nothing written ({applied} change(s) planned, '
                    f'{skipped} already done). Re-run with --apply.'))
            else:
                self._say(self.style.SUCCESS(
                    f'APPLIED {applied} change(s), {skipped} already done; '
                    f'sales tables verified unchanged.'))

    def _one_item(self, name):
        items = Item.objects.filter(name=name, is_active=True)
        self._guard(items.count() == 1, f'{items.count()} active items named {name!r}')
        return items.get()

    def _guard(self, ok, msg):
        if not ok:
            raise CommandError(f'PRECONDITION FAILED — aborted, nothing written: {msg}')
