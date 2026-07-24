"""PROD recipe/menu hygiene (2026-07-21): three manager-confirmed corrections.

Companion to fix_ingredient_structure (record structure) and
fix_ingredient_costing (cost values). This one fixes menu/recipe wiring that the
now-accurate cost engine surfaced, from the manager's answers (2026-07-21):

  * Nata (id 44): bought as a 2.5 kg tub, "same as fruit jelly". Fruit jelly is
    configured purchase_unit=Kilograms, factor 108 (scoops per tub), P280/tub ->
    cost P2.59/scoop. Nata's cost is ALREADY P2.59 (copied) but it had no
    purchase config, so a package restock/count could not convert. Mirror fruit
    jelly's config onto Nata. (Cosmetic: the 'Kilograms' label is fruit jelly's
    existing convention — the manager enters "1" per tub; factor 108 + P280 is
    what drives the P2.59 cost.)

  * Milk Tea - Classic phantom "12oz" line: recipe line for the Dabba-cup-size
    "12oz" option, a group NOT offered on milk tea (milk tea uses "U - Cup size":
    16oz/22oz). 12oz cannot be selected on a milk tea, so the line is dead. The
    manager confirms 12oz does not exist for milk tea -> delete the phantom line.

  * Milk Tea requires a size: the "U - Cup size" group is not marked required, so
    a milk tea could be rung up with NO size -> its size-scoped recipe matched
    nothing -> the sale snapshotted P0 cost (the one remaining zero-cost line).
    Enforcement (ISSUE-073) already exists; it just needs the flag. Set the
    required override on the Milk Tea CATEGORY only (targeted, reversible). The
    other four drink categories sharing this group (Coffee Frappe/Frappe/Fruit
    Tea/Latte) have the same latent gap — left for a separate, briefed decision.

NOT covered here (needs one more manager number): the Fries recipe re-encode
(potato by size, flavor powder per flavor) — the per-serving seasoning quantity
is unknown and will not be invented onto the live register.

NEVER touches a sales record — asserted at runtime with the same fingerprint
guard as its companions. Every step is guarded by the expected CURRENT value:
already-in-target-state -> skipped (idempotent), anything else -> loud abort,
nothing written. Dry-run by default:

    python manage.py fix_recipe_prod            # plan only
    python manage.py fix_recipe_prod --apply
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from pos.models import (
    CategoryVariantGroup, Ingredient, Item, ItemCategory, RecipeIngredient,
    PosTransaction, PosTransactionItem, PaymentLine, ZReport,
)

D = Decimal

NATA_PK, FRUIT_JELLY_PK = 44, 94
MT_CLASSIC = 'Milk Tea - Classic'
PHANTOM_GROUP = 'Dabba cup size'      # not offered on milk tea
SIZE_GROUP = 'U - Cup size'           # the shared 16/22oz size group

# The categories whose recipes are ENTIRELY size-scoped (no base line), so a
# sale with no size matches nothing and snapshots P0. Verified on the box
# 2026-07-21: Milk Tea (9), Latte (8), Fruit Tea (5) — all size-scoped-only.
# Coffee Frappe / Frappe share the same group but have BASE recipes (cost
# regardless of size), so they are NOT at risk and are deliberately left
# unforced to avoid pointless cashier friction.
REQUIRE_SIZE_CATEGORIES = ['Milk Tea', 'Latte', 'Fruit Tea']


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


class Command(BaseCommand):
    help = "PROD one-time recipe/menu correction (Nata config, MT phantom line, MT require size)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes. Omit for a dry run.')

    def _say(self, msg):
        self.stdout.write(msg)

    def _guard(self, ok, msg):
        if not ok:
            raise CommandError(f'PRECONDITION FAILED — aborted, nothing written: {msg}')

    def _one_item(self, name):
        items = Item.objects.filter(name=name, is_active=True)
        self._guard(items.count() == 1, f'{items.count()} active items named {name!r}')
        return items.get()

    def handle(self, *args, **options):
        apply_ = options['apply']
        with transaction.atomic():
            before = _sales_fingerprint()
            skipped = applied = 0

            # 1. Nata purchase config — mirror fruit jelly (factor 108, P280/tub).
            fj = Ingredient.objects.get(pk=FRUIT_JELLY_PK)
            self._guard(fj.name == 'Fruit jelly'
                        and fj.purchase_to_base_factor == 108
                        and fj.last_purchase_price == D('280'),
                        f'Fruit jelly template unexpected: unit={fj.purchase_unit_id} '
                        f'factor={fj.purchase_to_base_factor} pp={fj.last_purchase_price}')
            nata = Ingredient.objects.select_for_update().get(pk=NATA_PK)
            self._guard(nata.name == 'Nata', f'pk {NATA_PK} is {nata.name!r}, not Nata')
            self._guard(nata.cost_per_unit == D('2.59'),
                        f'Nata cost is {nata.cost_per_unit}, expected 2.59 (=280/108)')
            if nata.purchase_to_base_factor == 108 and nata.last_purchase_price == D('280'):
                self._say('  ~ Nata purchase config already set (factor 108, P280) — skip')
                skipped += 1
            else:
                self._guard(
                    nata.purchase_to_base_factor is None and nata.last_purchase_price is None
                    and nata.purchase_unit_id is None,
                    f'Nata already partially configured: unit={nata.purchase_unit_id} '
                    f'factor={nata.purchase_to_base_factor} pp={nata.last_purchase_price}')
                nata.purchase_unit_id = fj.purchase_unit_id
                nata.purchase_to_base_factor = 108
                nata.last_purchase_price = D('280')
                nata.save(update_fields=['purchase_unit', 'purchase_to_base_factor',
                                         'last_purchase_price'])
                self._say('  * Nata: purchase config set (2.5kg tub -> 108 scoops @ P280, '
                          'mirroring fruit jelly)')
                applied += 1

            # 2. Milk Tea - Classic: delete the phantom Dabba "12oz" recipe line.
            mt = self._one_item(MT_CLASSIC)
            phantom = RecipeIngredient.objects.filter(
                item=mt, variant__group__name=PHANTOM_GROUP)
            if not phantom.exists():
                self._say('  ~ Milk Tea phantom 12oz line already gone — skip')
                skipped += 1
            else:
                opts = sorted({(l.variant.name if l.variant else None) for l in phantom})
                self._guard(opts == ['12oz'],
                            f'MT {PHANTOM_GROUP!r} lines unexpected options {opts}, expected [12oz]')
                n = phantom.count()
                phantom.delete()
                self._say(f'  * Milk Tea - Classic: deleted {n} phantom "{PHANTOM_GROUP}/12oz" '
                          'recipe line(s) — 12oz is not on the milk tea menu')
                applied += 1

            # 3. Require a size on the size-scoped-only categories (Milk Tea,
            #    Latte, Fruit Tea) via a targeted category override — the exact
            #    scope of the P0 no-size bug. Frappes have base recipes -> left
            #    unforced. Brief the manager: these drinks now require a size tap.
            for cat_name in REQUIRE_SIZE_CATEGORIES:
                cats = ItemCategory.objects.filter(name=cat_name)
                self._guard(cats.count() == 1,
                            f'{cats.count()} categories named {cat_name!r}, expected 1')
                cvg = CategoryVariantGroup.objects.filter(
                    category=cats.get(), group__name=SIZE_GROUP)
                self._guard(cvg.count() == 1,
                            f'{cvg.count()} {SIZE_GROUP!r} attachments on {cat_name!r}, expected 1')
                row = cvg.get()
                if row.is_required_override is True:
                    self._say(f'  ~ {cat_name} size already required — skip')
                    skipped += 1
                    continue
                self._guard(row.is_required_override is None,
                            f'{cat_name} size required_override is {row.is_required_override!r}, '
                            'expected None')
                row.is_required_override = True
                row.save(update_fields=['is_required_override'])
                self._say(f'  * {cat_name}: size ("{SIZE_GROUP}") is now REQUIRED '
                          '(cashiers must pick a size) — brief the manager')
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
