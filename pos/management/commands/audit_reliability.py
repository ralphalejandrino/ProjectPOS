"""Read-only reliability audit for the live register (2026-07-18).

Exercises the real weekly-report code path on live data (a genuine crash test,
not a reasoned guess) and independently reconciles the money against the raw
rows, then checks post-dedup recipe/structure integrity and cost coverage.

WRITES NOTHING — every query is a read. Safe to run mid-shift.

    python manage.py audit_reliability            # audits the most-recent COMPLETED week
    python manage.py audit_reliability --week 2026-07-15
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum, Count
from django.utils import timezone

from pos.models import (
    PosTransaction, PosTransactionItem, PaymentLine, ZReport,
    Ingredient, RecipeIngredient, Item,
)
from pos.views import _weekly_payload, _resolve_week, _aggregate_transactions

Z = Decimal('0.01')


class Command(BaseCommand):
    help = "Read-only reliability audit: report crash test + money + structure reconciliation."

    def add_arguments(self, parser):
        parser.add_argument('--week', type=str, default=None,
                            help='Any date YYYY-MM-DD inside the week to audit.')

    def _ok(self, msg):
        self.stdout.write(self.style.SUCCESS('  PASS  ') + msg)

    def _fail(self, msg):
        self.fail_count += 1
        self.stdout.write(self.style.ERROR('  FAIL  ') + msg)

    def _info(self, msg):
        self.stdout.write('  ..    ' + msg)

    def handle(self, *args, **opt):
        self.fail_count = 0
        today = timezone.localdate()
        if opt['week']:
            from datetime import datetime
            anchor = datetime.strptime(opt['week'], '%Y-%m-%d').date()
        else:
            # most recent COMPLETED Sat–Fri week (what the report opens on)
            anchor = _resolve_week(today)[0] - timedelta(days=1)
        wk_start, wk_end = _resolve_week(anchor)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== RELIABILITY AUDIT — week {wk_start} .. {wk_end} (today {today}) ===\n'))

        self._section_report(anchor, wk_start, wk_end)
        self._section_money()
        self._section_zreports()
        self._section_structure()
        self._section_coverage(wk_start, wk_end)

        self.stdout.write('')
        if self.fail_count == 0:
            self.stdout.write(self.style.SUCCESS(
                '=== AUDIT CLEAN — 0 failures. No crashes, no money mismatches, no bad recipe refs. ==='))
        else:
            self.stdout.write(self.style.ERROR(
                f'=== AUDIT FOUND {self.fail_count} FAILURE(S) — see FAIL lines above. ==='))

    # -- 1. weekly report: crash test + reconciliation --------------------
    def _section_report(self, anchor, wk_start, wk_end):
        self.stdout.write(self.style.MIGRATE_HEADING('1) WEEKLY REPORT — execute + reconcile'))
        try:
            payload, err = _weekly_payload(anchor.strftime('%Y-%m-%d'))
        except Exception as e:  # noqa: BLE001 - this is the crash test
            self._fail(f'_weekly_payload raised {type(e).__name__}: {e}')
            return
        if err:
            self._fail(f'_weekly_payload returned error: {err}')
            return
        self._ok('weekly report computed without error (no crash)')
        self.payload = payload

        s = payload['summary']
        self._info(f"summary: gross {s['gross_total']}  net {s['net_total']}  "
                   f"txns {s['transaction_count']}  voids {s['void_count']}  "
                   f"avg_ticket {s['avg_ticket']}")
        # per-day gross must sum to the summary gross
        day_gross = sum(Decimal(d['gross']) for d in payload['days'])
        day_txns = sum(d['transaction_count'] for d in payload['days'])
        if day_gross == Decimal(s['gross_total']):
            self._ok(f"per-day gross sums to summary gross ({s['gross_total']})")
        else:
            self._fail(f"per-day gross {day_gross} != summary gross {s['gross_total']}")
        if day_txns == s['transaction_count']:
            self._ok(f"per-day txn counts sum to summary count ({s['transaction_count']})")
        else:
            self._fail(f"per-day txns {day_txns} != summary count {s['transaction_count']}")

        # independent recompute straight from the raw rows for the same window
        raw = (PosTransaction.objects.filter(
                    created_at__date__gte=wk_start, created_at__date__lte=wk_end,
                    is_seed=False, void=False, status='completed')
               .exclude(transaction_type='refund')
               .aggregate(g=Sum('gross_total'), n=Sum('net_total'), c=Count('id')))
        rg = Decimal(str(raw['g'] or 0)).quantize(Z)
        if rg == Decimal(s['gross_total']):
            self._ok(f"independent raw recompute matches summary gross ({rg})")
        else:
            self._fail(f"raw gross {rg} != summary gross {s['gross_total']}")
        if (raw['c'] or 0) == s['transaction_count']:
            self._ok(f"independent raw txn count matches ({raw['c'] or 0})")
        else:
            self._fail(f"raw count {raw['c']} != summary {s['transaction_count']}")

        # daily status labels: no future day marked 'final', today is 'live'
        bad = [d['date'] for d in payload['days']
               if (d['date'] > str(timezone.localdate()) and d['status'] != 'upcoming')]
        if not bad:
            self._ok('no future day mislabeled as final (silent-hole guard)')
        else:
            self._fail(f'future days not marked upcoming: {bad}')

    # -- 2. per-transaction money integrity -------------------------------
    def _section_money(self):
        self.stdout.write(self.style.MIGRATE_HEADING('2) PER-TRANSACTION MONEY INTEGRITY (all completed sales)'))
        sales = (PosTransaction.objects.filter(is_seed=False, void=False, status='completed')
                 .exclude(transaction_type='refund'))
        n = sales.count()
        line_bad, drawer_bad, pay_bad, neg_bad = [], [], [], []
        for t in sales.prefetch_related('items', 'payment_lines'):
            line_sum = sum((li.subtotal for li in t.items.all()), Decimal('0'))
            if line_sum.quantize(Z) != (t.gross_total or Decimal('0')).quantize(Z):
                line_bad.append((t.id, str(line_sum), str(t.gross_total)))
            for f in ('gross_total', 'discount_total', 'vat_amount', 'net_total'):
                v = getattr(t, f)
                if v is not None and v < 0:
                    neg_bad.append((t.id, f, str(v)))
            if (t.net_total or Decimal('0')) > (t.gross_total or Decimal('0')):
                neg_bad.append((t.id, 'net>gross', f"{t.net_total}>{t.gross_total}"))
            plines = list(t.payment_lines.all())
            if plines:
                psum = sum((p.amount for p in plines), Decimal('0'))
                if psum.quantize(Z) != (t.net_total or Decimal('0')).quantize(Z):
                    pay_bad.append((t.id, str(psum), str(t.net_total)))
            elif t.payment_method == 'cash' and t.cash_received is not None:
                drawer = (t.cash_received - (t.change_given or Decimal('0'))).quantize(Z)
                if drawer != (t.net_total or Decimal('0')).quantize(Z):
                    drawer_bad.append((t.id, str(drawer), str(t.net_total)))

        self._info(f'{n} completed sale transactions checked')
        for label, bad in (('line subtotals == gross_total', line_bad),
                           ('payment lines == net_total', pay_bad),
                           ('cash drawer (received-change) == net_total', drawer_bad),
                           ('no negative money / net<=gross', neg_bad)):
            if not bad:
                self._ok(label)
            else:
                self._fail(f'{label}: {len(bad)} bad — e.g. {bad[:3]}')

    # -- 3. Z-report internal consistency ---------------------------------
    def _section_zreports(self):
        self.stdout.write(self.style.MIGRATE_HEADING('3) Z-REPORT INTEGRITY'))
        zs = list(ZReport.objects.all().order_by('id'))
        self._info(f'{len(zs)} Z-reports')
        neg = [z.id for z in zs if (z.net_sales or 0) < 0]
        if not neg:
            self._ok('no Z-report has negative net_sales')
        else:
            self._fail(f'Z-reports with negative net_sales: {neg}')
        # grand_total_sales is a never-reset running accumulator -> non-decreasing by id
        prev, mono_bad = None, []
        for z in zs:
            g = z.grand_total_sales or Decimal('0')
            if prev is not None and g < prev:
                mono_bad.append(z.id)
            prev = g
        if not mono_bad:
            self._ok('grand_total_sales monotonic non-decreasing (BIR accumulator)')
        else:
            self._fail(f'grand_total_sales DECREASED at Z ids: {mono_bad}')

    # -- 4. recipe / structure integrity (post-dedup) ---------------------
    def _section_structure(self):
        self.stdout.write(self.style.MIGRATE_HEADING('4) RECIPE / STRUCTURE INTEGRITY (post-dedup)'))
        # THE key post-correction invariant: no recipe references an inactive ingredient
        bad = (RecipeIngredient.objects.filter(ingredient__is_active=False)
               .select_related('ingredient', 'item'))
        if not bad.exists():
            self._ok('no recipe line references an INACTIVE ingredient')
        else:
            rows = [(r.id, r.ingredient_id, r.ingredient.name,
                     getattr(r.item, 'name', None)) for r in bad[:20]]
            self._fail(f'{bad.count()} recipe line(s) -> inactive ingredient: {rows}')
        # recipe ingredients that are active but cost 0 (uncosted -> flatter margin)
        zerocost = (Ingredient.objects.filter(
                        is_active=True, recipes__isnull=False, cost_per_unit=0)
                    .distinct().values_list('id', 'name'))
        if zerocost:
            self._info(f'{len(zerocost)} active recipe-used ingredient(s) with cost 0 '
                       f'(known COGS gap): {list(zerocost)}')
        else:
            self._ok('every active recipe-used ingredient has a nonzero cost')
        # recipe coverage of active sellable items
        active_items = Item.objects.filter(is_active=True)
        with_recipe = active_items.filter(recipe_ingredients__isnull=False).distinct().count()
        self._info(f'{with_recipe}/{active_items.count()} active items have a recipe')

    # -- 5. cost coverage / margin honesty --------------------------------
    def _section_coverage(self, wk_start, wk_end):
        self.stdout.write(self.style.MIGRATE_HEADING('5) COST COVERAGE / MARGIN HONESTY'))
        lines = PosTransactionItem.objects.filter(
            pos_transaction__created_at__date__gte=wk_start,
            pos_transaction__created_at__date__lte=wk_end,
            pos_transaction__void=False, pos_transaction__is_seed=False)
        total = lines.count()
        non_null = lines.filter(unit_cost__isnull=False).count()     # what _weekly_cogs counts
        non_zero = lines.filter(unit_cost__gt=0).count()             # genuinely costed
        nn_ratio = round(non_null / total, 3) if total else None
        nz_ratio = round(non_zero / total, 3) if total else None

        # What the report ACTUALLY shows her (read straight off the payload).
        prof = getattr(self, 'payload', {}).get('profitability', {}) if hasattr(self, 'payload') else {}
        rep_ratio = prof.get('costed_line_ratio')
        rep_margin = prof.get('gross_margin_pct')
        rep_cogs = prof.get('cogs')
        self._info(f'report shows: margin {rep_margin}%  COGS {rep_cogs}  '
                   f'costed_line_ratio {rep_ratio}  (=_weekly_cogs non-null count)')
        self._info(f'coverage non-null (unit_cost set): {non_null}/{total} = {nn_ratio}')
        self._info(f'coverage GENUINE (unit_cost > 0):  {non_zero}/{total} = {nz_ratio}')

        margin_shown = (rep_ratio is None or rep_ratio >= 0.9)  # period.html #10 gate
        if margin_shown and nz_ratio is not None and nz_ratio < 0.9:
            self._fail(
                f'#10 HOLE: report shows a CONFIDENT margin ({rep_margin}%) because '
                f'costed_line_ratio counts unit_cost=0 lines as covered ({nn_ratio}), but only '
                f'{nz_ratio:.0%} of lines are genuinely costed — {non_null - non_zero} lines '
                f'contribute 0 COGS -> the margin is FLATTERED. #10 should count unit_cost>0.')
        elif not margin_shown:
            self._ok(f'report shows margin N/A (coverage {rep_ratio} < 0.9) — honest')
        else:
            self._ok(f'report shows a margin backed by {nz_ratio:.0%} genuinely-costed lines')

        # zero-cost active recipe ingredients already listed in section 4 drive this.
