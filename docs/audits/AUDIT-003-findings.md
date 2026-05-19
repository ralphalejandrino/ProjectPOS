# AUDIT-003 — X/Z Reporting System End-to-End Investigation

**Auditor:** Claude (automated audit)
**Date:** 2026-05-16
**Repo:** `/home/ralph/TarsierPOS`
**Scope:** Read-only audit. No code, migrations, settings, or data modified. Nothing committed or pushed.
**Builds on:** AUDIT-001 (no ingredient-consumption ledger; `ItemLog` has no `sale` rows; FEATURE-009 blocks COGS/inventory), AUDIT-002 (variant `price_modifier` is functional and flows to `subtotal`/`total_amount`). Settled territory not re-investigated.

---

## 1. TL;DR

**Reports are not usable today for a remote, every-other-day cafe owner — and the "Gross Sales" number on the Z report is not gross sales.** The X report is a per-cashier, current-open-shift JSON summary (4 cards + payment split). The Z report is a **per-calendar-day** aggregate (PHT) with reasonable operational breakdowns (payment method, cashier, top items, voids, discounts). Neither is tied to a Z-counter, neither is persisted, and closing a shift does **not** finalize or snapshot anything — the Z report is a pure read-time recompute, so a later void of an old sale silently rewrites a historical day's Z report.

The headline gap: the Z report's **"Gross Sales" line is actually net of discounts** (verified on live data: 2026-05-15 shows `gross_sales=1004` while the true pre-discount line-item total is `1150`, with `146` of discounts then *also* listed separately). There is no true gross line, no Z counter, no cumulative grand total, no MIN/serial/accreditation number, and no permanent storage of any Z report. For the remote owner specifically: access is Tailscale-only (laptop/phone on the tailnet), there is no read-only "owner/viewer" role (Z requires manager/admin), no email/PDF/CSV export, no scheduled digest, no notification path, and no multi-day rollup beyond a separate Dashboard view. BIR-style fields are largely absent or hardcoded.

Most of what an owner would want (top items, peak hours, per-cashier, voids-per-cashier) **exists in the data** and is partly computed already — these are UI/query gaps, not data gaps. The genuinely data-blocked items (COGS margin, ingredient reorder) depend on FEATURE-009 per AUDIT-001.

---

## 2. Current X Report Content

**Code path:** `canteen/views.py:570-626` (`PosTransactionViewSet.xreport`, GET, `IsCashierOrAbove`). Rendered by `frontend/public/xreport.html`. ESC/POS variant: `canteen/receipt_service.py:260-312` (`print_xreport_summary`), triggered via `views.py:354-361` (`print_xreport`).

**Shift boundary:** the *current open `Shift` for the requesting user* — `Shift.objects.filter(cashier=request.user, is_open=True).first()` (`views.py:577-579`). Returns HTTP 404 if no open shift (`views.py:581-585`). It is **per-cashier and per-open-shift**, not per-day. `Shift` model: `models.py:768-781` (`opened_at` auto, `closed_at`, `opening_cash`, `closing_cash`, `is_open`).

| Section | Data shown | Source / formula |
|---|---|---|
| Shift info | cashier username, `opened_at`, `generated_at` | `views.py:615-618`; `xreport.html:231-233` |
| Gross Sales card | `Sum(total_amount)` of `void=False, status='completed'` in this shift | `views.py:590,593-594` |
| Net Sales card | `round(gross, 2)` — **identical to gross** (no deductions applied) | `views.py:599` |
| Transactions card | `completed.count()` | `views.py:595` |
| Voids card | `voided.count()` where `void=True` in shift | `views.py:591,596` |
| Payment Breakdown table | count + `Sum(total_amount)` for `cash`/`gcash`/`maya` (no `card`) | `views.py:603-612` |
| Void Total | `Sum(total_amount)` of voided in shift | `views.py:597-598` |
| Average Transaction | `gross / transaction_count` | `views.py:600-601` |

**Derived metrics (exact formulas):**
- `gross_sales = Sum(PosTransaction.total_amount where shift=shift, void=False, status='completed')`
- `net_sales = round(gross_sales, 2)` — *no discount/void/VAT subtraction*
- `average_transaction = round(gross_sales / transaction_count, 2)`
- `void_total = Sum(total_amount where shift=shift, void=True)` (informational; not subtracted)

X report has **no** VAT, discount, top-items, hourly, cashier-comparison, OR-range, or business-identity content.

---

## 3. Current Z Report Content

**Code path:** `canteen/views.py:363-568` (`PosTransactionViewSet.zreport`, GET, `IsManagerOrAbove`). Rendered by `frontend/public/zreport.html`. ESC/POS variant: `receipt_service.py:185-257` (`print_zreport_summary`), triggered via `views.py:345-352` (`print_zreport`).

**Shift boundary:** **none.** The Z report is keyed by **calendar date** — `PosTransaction.objects.filter(created_at__date=report_date)` (`views.py:376`), where `report_date` defaults to today or comes from `?date=YYYY-MM-DD` (`views.py:370-374`). `created_at__date` and `ExtractHour` resolve in **PHT** (`pos_config/settings.py:141` `TIME_ZONE='Asia/Manila'`, `:145` `USE_TZ=True`), which matches the OR-counter PHT rollover (`models.py:467-468`). The Z report is **decoupled from `Shift`** entirely — closing a shift (`views.py:1407-1442`) only sets `closing_cash/closed_at/is_open` and reloads today's date Z report in the UI (`zreport.html:602-606`); it does not finalize, snapshot, increment a counter, or freeze data.

**X vs Z distinction in code:** separate view methods, separate URLs (`/transactions/xreport/` vs `/transactions/zreport/`), separate templates, separate ESC/POS functions. **Z does not mutate state** — it is a read-only recompute. There is no "Read vs Reset" semantic; "Z" is naming only.

| Section (template) | Data shown | Source / formula (`views.py`) |
|---|---|---|
| B — Report header | business name (from DOM `#site-name`, **not** API), date, generated_at, generated_by (= request.user), opening/closing txn #, first/last sale time | `:538-543,557-560`; `zreport.html:404-415` |
| C — Sales Summary | Gross Sales, Total Void Amount, **NET SALES**, Total Transactions, Total Items Sold, Average Transaction, Busiest Hour | `:546-549,545,555,561` |
| D — VAT Breakdown | Net of VAT, VAT-Exempt Sales (SC/PWD), VAT Amount, Gross (Total) | `:550-554` |
| E — Payment Methods | per-method count + subtotal for `cash`/`gcash`/`maya` (**no `card`**) + total | `:456-464` |
| F — Cash Management | Cash Expected in Drawer; Over/Short = blank manual line | `:466-467`; `zreport.html:218-221` |
| G — Sales by Cashier | per-cashier name, sale count, subtotal, **void count** (whole day) | `:469-490` |
| H — Top Selling Items | top 5 by `Sum(quantity)`, with `Sum(subtotal)` revenue | `:492-508` |
| I — Void Summary | void count, void total, per-void list (txn no, amount, voided_by, voided_at) | `:420-433,547-548,565` |
| J — Discounts Given | per `discount_type` count + `Sum(discount_amount)`; hidden if zero | `:435-451,566-567` |

**Derived metrics (exact formulas):**
- `gross_sales = Sum(total_amount where created_at__date=D, void=False, status='completed')` — **already net of discounts and net of SC/PWD VAT removal**, because `total_amount` is stored as `final_total` after discount/VAT-exempt subtraction (`services.py:262-299`). **Mislabeled as "Gross".**
- `net_sales = round(gross_sales, 2)` (`views.py:454`) — equal to "gross"; voids already excluded by the `void=False` filter; `void_total` is informational only (comment `views.py:453`).
- `vat_exempt_sales = Sum(total_amount where discount_type in ['sc','pwd'])` (`views.py:391-394`) — derived from `discount_type`, **not** from the model's `vat_exempt` boolean (`models.py:425-428`).
- VAT (only if `BusinessProfile.vat_enabled`): `_vatable = gross − exempt`; inclusive → `vat = vatable * rate/(100+rate)`, `net_of_vat = gross − vat`; exclusive → `vat = vatable * rate/100`, `net_of_vat = gross` (`views.py:396-418`). VAT is currently **disabled** in live data, so this card renders zeros (`vat_rate:null`).
- `cash_expected = ` cash-method `Sum(total_amount)` only (`views.py:467`) — **excludes `Shift.opening_cash` float and any payouts.**
- `average_transaction = round(gross/transaction_count, 2)`; `busiest_hour = ExtractHour(created_at)` mode (PHT); `total_items_sold = Sum(PosTransactionItem.quantity)`.

**Print entry points:** browser print (CSS `@media print` in both templates, `zreport.html:15-20`) **and** ESC/POS thermal (`receipt_service.py`, `RECEIPT_WIDTH`-padded rows, e.g. `:211-212`). 58mm width is enforced via `RECEIPT_WIDTH` padding. Note the ESC/POS Z print omits VAT, top items, busiest hour, cashier breakdown, first/last time, and OR range that the HTML shows — the two Z renderings are **not field-equivalent**.

---

## 4. BIR Field Mapping Table (Phase 2)

Reports only what the code does/does not contain. No assertion of what BIR requires.

| BIR field category | Present? | Where (file:line) | Notes |
|---|---|---|---|
| Sequential OR numbering (no gaps) | Partial | `models.py:462-476` | `OR-YYYYMMDD-NNNN`, per-day `OfficialReceiptCounter` (`models.py:247-259`), **counter resets daily**. Live: recent real days 05-13…05-16 are gap-free 1..N; but 346/368 seed txns have explicit `transaction_no` bypassing the counter (counter has only 29 increments for 368 txns). No global monotonic series. |
| Beginning OR number for shift | Partial | `views.py:533-534,559` | `opening_txn_no` = first completed txn of the **day** (not shift). |
| Ending OR number for shift | Partial | `views.py:535,560` | `closing_txn_no` = last completed txn of the day. |
| Z counter (incremented per Z read) | **No** | — | No Z-counter field anywhere; Z report is a stateless recompute. |
| Reset counter | **No** | — | No reset concept; Z does not reset anything. |
| Gross sales | **No (mislabeled)** | `views.py:546,381-382` | "Gross Sales" = `Sum(total_amount)` which is **post-discount/post-VAT-exempt net**. True pre-discount gross is never computed. |
| Net sales | Partial | `views.py:454,549` | `net_sales = round(gross,2)`; equal to the (mislabeled) gross. |
| VAT-able sales (12%) | Partial | `views.py:399,436,554` | `net_of_vat` only when `vat_enabled`; currently disabled → 0. |
| VAT-exempt sales | Partial | `views.py:391-394,553` | Derived from `discount_type in (sc,pwd)`, not from `vat_exempt` flag; value present even when VAT disabled. |
| Zero-rated sales | **No** | — | No zero-rated concept in model or report. |
| VAT amount | Partial | `views.py:402-414,552` | Computed only if `vat_enabled`; 0 in live data. |
| SC (Senior Citizen) discount line | Yes | `views.py:436-448`; `models.py:411` | In discount breakdown when present (live 05-15: sc count 1, ₱32). |
| PWD discount line | Yes | `views.py:436-448`; `models.py:412` | Live 05-15: pwd count 4, ₱114. |
| Other discount lines (employee, loyalty, promo) | Partial | `views.py:439-448`; `models.py:408-414` | `promo`/`fixed`/`percentage` mapped to "Promo"/"Other". No employee/loyalty types exist. |
| Voided transactions: count + total | Yes | `views.py:420-433,547-548` | Count + `Sum(total_amount)` + per-void list with `voided_by`. |
| Refund: count + total | **No** | `views.py:239` | `'refunded'` referenced only in a void guard; not a status choice (`models.py:294-298`), no refund flow, not in report. |
| Accumulated grand total (since installation) | **No** | — | No cumulative/running grand-total field or computation. |
| Machine Identification Number (MIN) | **No** | `models.py:719-752` | Not on `BusinessProfile`; not in report. |
| Serial number | **No** | `models.py:719-752` | Absent. |
| Accreditation number | **No** | `models.py:719-752` | Absent. |
| Operator/cashier identification | Partial | `views.py:538,469-490` | `generated_by` = report-runner; per-cashier breakdown is whole-day aggregate, not OR-range-attributed. |
| "Read" vs "Reset" (X vs Z) distinction | Partial | `views.py:363,570` | Distinct endpoints/templates, but Z performs no reset; label-only. |
| Date/time of report generation | Yes | `views.py:542` | `generated_at = timezone.now()`. |
| Business name + TIN | Partial | `receipt_service.py:195,48-50`; `zreport.html:405-406` | ESC/POS prints `business_name` + `TIN` (live: "Tarsier Demo Cafe", TIN `123-456-789-000`). **HTML Z report pulls business name from a DOM element, not the API, and never shows TIN.** |

Hardcoded/defaulted/missing-live-value flags: business name in HTML is DOM-sourced (`zreport.html:405`); currency hardcoded `'PHP'`/`₱` everywhere (`services.py:282`, `zreport.html:337`, `receipt_service.py` `PHP`); MIN/serial/accreditation/Z-counter/grand-total have **no field at all**.

---

## 5. Data Accuracy Findings (Phase 3)

Live DB: 368 `PosTransaction` (364 completed, 4 void). Only 22 completed in last 14 days (2026-05-13..16); ~346 are March seed data. `is_seed`/FLAG-047 **not shipped** (no `is_seed` field in any `canteen/*.py`), so seed and real data are indistinguishable in aggregates.

1. **"Gross Sales" is net of discounts (ISSUE-class).** Reproduce: `zreport(date=2026-05-15)` → `gross_sales=1004`, `total_discounts_given=146`. Recompute from raw line items: `Sum(PosTransactionItem.subtotal)` for that day = **1150**; `1150 − 146 = 1004`. The report shows `1004` *as* "Gross Sales" **and** lists the `146` discounts separately (Section J) — a reader cannot recover true gross, and a naive "gross − discounts" double-subtracts. The VAT card additionally labels the same post-discount sum "Gross Sales (Total)" (`zreport.html:188-189`).

2. **Voids — prior-shift/prior-day handling is retroactive (ISSUE-class).** Void (`views.py:228-272`) sets `void=True, voided_at=now, status='void'` but does **not** alter `total_amount` or stamp the report period. The Z report filters voids by `created_at__date` (`views.py:376-378`), so a void executed today against a sale created on a prior day lands in the **original creation day's** Z report, not today's. Because the Z report is recomputed live every time (no snapshot/persistence), **re-opening a past date's Z report after a later void silently changes that historical day's gross and void totals.** Void count/total correctly use the original sale amount and exclude the original from gross (single-shift case is correct). Live: 4 voids exist, all March seed; 0 voids in last 30 days, so the live Z captures show empty void sections (correct rendering).

3. **Multi-payment-method: not modeled.** `PosTransaction.payment_method` is a single `CharField` (`models.py:308-313`); there is no split-payment table. A cash+GCash split cannot be represented; reports attribute the entire `total_amount` to one method. Not a computation bug — a data-model limitation that makes the payment breakdown unable to ever reflect split tenders.

4. **SC/PWD VAT-exempt accounting is inconsistent with the model.** `vat_exempt_sales` is derived from `discount_type in (sc,pwd)` (`views.py:391-394`), **not** from `PosTransaction.vat_exempt`/`vat_amount`. Live 05-15: 5 SC/PWD txns, report `vat_exempt_sales=584`, yet every one of those rows has `vat_exempt=False, vat_amount=0.00` (because `BusinessProfile.vat_enabled=False`, so `services.py:226-240` never set the flag). So the figure is a discount-type proxy, divergent from the model's own VAT-exempt fields. When VAT is enabled the SC/PWD path *does* compute `_sc_pwd_vat_amount` (`services.py:226-240`) but the Z report ignores that stored value and re-derives.

5. **Currency hardcoded.** `BusinessProfile.currency` (`models.py:742`, default `PHP`) is never consulted by reports. `services.py:282` hardcodes `Money(final_total, 'PHP')`; `zreport.html:337` / `xreport.html:192` hardcode `₱` and `en-PH`; ESC/POS prints literal `PHP`. Non-PHP configuration is silently ignored.

6. **Multi-cashier shifts: Z aggregates per-day with a per-cashier breakdown (correct); X is single-cashier-only.** Live 05-15 Z shows two cashiers (Ralph ₱584/5, admin ₱420/3) — accurate split with void counts. X report only ever reflects the requester's own open shift.

7. **Variant `price_modifier` flows correctly.** Per AUDIT-002 and `services.py:186,194-196,308-313`, modifiers are in `final_price`/`subtotal`/`total_amount`. Z `top_items.revenue = Sum(subtotal)` and gross therefore include modifiers correctly. No discrepancy found.

8. **Zero-quantity edge cases render gracefully.** No transactions → `transaction_count:0`, frontend shows "No transactions found" (`zreport.html:386-390`). No open shift → X returns 404 with a friendly panel (`xreport.html:213-221`). Empty cashier/items/voids → explicit empty-state rows (`zreport.html:465-466,480-481,517-519`).

Additional accuracy notes:
- **`cash_expected` excludes the opening float.** It is cash-method sales only (`views.py:467`); `Shift.opening_cash` is never added and the Z report is not shift-bound, so Over/Short reconciliation against a physical drawer is structurally incomplete.
- **OR integrity is clean for real recent data** (05-13..16 each gap-free `0001..000N`, no duplicate `transaction_no` across all 368), but the daily-reset counter plus seed bypass means there is no installation-wide sequential guarantee.
- **`ItemLog` has zero `sale` rows** (only 3 `adjustment`) — confirms AUDIT-001/ISSUE-069. Reports don't depend on `ItemLog` (they use `PosTransactionItem`), so report figures are unaffected, but there is no independent reconciliation trail.

---

## 6. Multi-Day / Historical Capability (Phase 4)

| Capability | State | Evidence |
|---|---|---|
| Z report archive (past Z by date) | **No persistence; ad-hoc recompute** | `zreport(?date=)` recomputes live (`views.py:370-376`). No `ZReport`/`ShiftReport` model exists (`models.py` has none). Past dates viewable only by re-querying; results are mutable (see §5.2). |
| "Last 7 / month / last month" totals | Partial (separate Dashboard) | `DashboardViewSet` (`views.py:686-811`): today/week/month/all-time revenue + counts, last-7-day daily revenue, top-5 items today; consumed by `frontend/public/dashboard.html:339`. |
| Dashboard / analytics view | Yes (two) | (a) `DashboardViewSet` sales stats (above). (b) `ItemViewSet.analytics` (`views.py:1194-1224`) = **inventory** analytics only: item count, low-stock count (by `Item.stock`), inventory value, item-level profit margin from `Item.purchase_price` (not recipe COGS — accuracy blocked by FEATURE-009 per AUDIT-001). This is the FLAG-012 `analytics` action. |
| Per-day / week / month breakdown | Partial | Dashboard gives day granularity for last 7 days only; week/month are single roll-up totals. No per-week or per-month series. |
| Time-series / trend / period comparison | **No** | `last_7_days` is a value list with no chart, no trend line, no prior-period comparison. `dashboard.html` consumes it but no comparative analytics. |
| Export (CSV/PDF/email) | **No** | Only `import_csv` for items (`views.py:1283-1292`); no report export anywhere. |
| Permanent Z storage (10-yr retention class) | **No** | Z reports are never written to the DB. `Shift` rows persist (`views.py:1380-1385` blocks deletion) but carry no sales/VAT/discount snapshot. |

---

## 7. Owner-Insight Gap Matrix (Phase 5)

| # | Owner ask | Answer | Where / classification |
|---|---|---|---|
| 1 | Top 10 best-selling items this week | **No** | Z top-items is **single-day, top 5** (`views.py:492-508`); Dashboard top-items is **today, top 5** (`views.py:752-770`). No weekly window, no top-10. **UI/query gap — data available.** |
| 2 | Peak hours by day-of-week | **No** | Z gives one `busiest_hour` for one day (`views.py:517-525`). No DOW×hour matrix. **UI/query gap — `created_at` available.** |
| 3 | Items running low on stock | Partial | `ItemViewSet.analytics` returns `low_stock_count` by `Item.stock` (`views.py:1205`); not surfaced in X/Z. Ingredient-level low stock exists on the model (`Ingredient.is_low_stock`, `models.py:830-833`) but is **unreliable** per AUDIT-001 (no consumption ledger). **Partial: item-level UI gap; ingredient-level data gap → FEATURE-009.** |
| 4 | Sales per cashier this week | Partial | Z has per-cashier **for one day** (`views.py:469-490`). No weekly. **UI/query gap — data available.** |
| 5 | Voids per cashier | Partial | Z `by_cashier.void_count` exists **per day** (`views.py:477,487`). No multi-day trend. **UI/query gap — data available.** |
| 6 | Margin per item, COGS-based | **No** | Only static item-level margin from `Item.purchase_price` (`views.py:1210-1215`); no recipe/COGS margin. **Data gap → FEATURE-009 + recipe coverage (AUDIT-001).** |
| 7 | Today vs same day last week | **No** | No comparative logic anywhere. **UI/query gap — data available.** |
| 8 | Anomalies (unusual voids / low sales / missing txns) | **No** | No anomaly detection, no expected-baseline, no gap detection on OR series. **UI/query gap — data available.** |
| 9 | What to reorder tomorrow | **No** | No reorder/par-vs-consumption logic surfaced; `Ingredient.par_level` exists but consumption is invisible. **Data gap → FEATURE-009 (AUDIT-001).** |
| 10 | Discount usage trend (SC/PWD claim rate over time) | **No** | Z shows per-day discount counts only (`views.py:435-448`); no time series. **UI/query gap — `discount_type`/date available.** |

7 of 10 are UI/query gaps over data that already exists; 2 are FEATURE-009 data gaps (#6, #9); #3 is split (item UI gap / ingredient data gap).

---

## 8. Delivery / Remote Access (Phase 6)

- **Network reach:** access is **Tailscale-only** — `https://100.100.100.100/` (`TARSIER_POS.md:12,100`), tailnet device required; `ufw` only opens 80/443 on `tailscale0` (`TARSIER_POS.md:349-350`). No public/internet endpoint. The owner must run Tailscale on their phone and be added to the tailnet; otherwise the report views are unreachable.
- **Export:** none — no email, PDF, or CSV export of X/Z (only the thermal printer at the shop and browser print).
- **Mobile rendering:** templates have `viewport` meta and Tailwind responsive classes (`zreport.html:5`, `grid-cols-1 sm:grid-cols-2`), so the HTML *scales*, but it is laid out for a wide report with multi-column tables; no phone-specific report layout.
- **Scheduled digest:** none — no `send_mail`, SMTP, Celery, cron, or webhook in `canteen/*.py` or `pos_config/*.py`.
- **Auth/roles:** roles are `admin`/`manager`/`cashier` only (`models.py:642-646,699-705`). Z report requires `IsManagerOrAbove` (`views.py:363`, `permissions.py:14-22`). **There is no read-only "owner/viewer" role** — a remote owner needs a manager or admin login (full write access) to see the Z report.
- **Offsite notification on Z finalize:** none — and there is no "finalize" event to notify on (Z is a stateless recompute; closing a shift fires no notification).

---

## 9. Live Data Sample (Phase 7)

Environment: 368 `PosTransaction` total (364 completed, 4 void), date range 2026-03-06 → 2026-05-16. `BusinessProfile`: `vat_enabled=False`, `vat_inclusive=True`, `vat_rate=12.00`, `currency=PHP`, `discounts_enabled=False`, `business_name='Tarsier Demo Cafe'`, `tin='123-456-789-000'`. 3 `Shift` rows, all `is_open=True`. Only 24/368 txns have a `shift` FK set.

**1. Completed txns/day, last 14 days (PHT; seed-diluted before 05-13):**
```
2026-05-13  count=2  amt=332
2026-05-14  count=4  amt=816
2026-05-15  count=8  amt=1004
2026-05-16  count=8  amt=1685
```
(No rows 05-03..05-12; ~346 March seed txns omitted from this window. `is_seed`/FLAG-047 not shipped — seed indistinguishable in aggregates.)

**2. Distinct cashiers, last 30 days:** 3 (all-time: 5).

**3. Hour-of-day distribution, last 30 days (completed, PHT):**
```
00:00  ## 2
01:00  ##### 5
08:00  # 1
13:00  #### 4
18:00  ### 3
19:00  #### 4
20:00  ### 3
```
(00:00–01:00 cluster = odd-hour test/seed-style data, not realistic café traffic.)

**4. Voided txns, last 30 days:** 0 (count) / total `None`. All 4 voids in the DB are March seed.

**5. SC/PWD discount txns, last 30 days:** 7. (Any `discount_amount>0` ever: 39.) `payment_method` distribution all-time: cash 234, gcash 77, maya 57. Status: completed 364, void 4.

**7. OR sequential integrity:** recent real days are gap-free and unique — 05-13 `[1,2]`, 05-14 `[1,2,3,4]`, 05-15 `[1..8]`, 05-16 `[1..8]`; zero duplicate `transaction_no` across all 368. **But** `OfficialReceiptCounter` has only 5 rows / 29 total increments (2026-03-21:7, 05-13:2, 05-14:4, 05-15:8, 05-16:8) for 368 transactions — seed/explicit-`transaction_no` txns bypass the counter, and the counter resets per PHT day, so there is no installation-wide monotonic OR series.

**6. Sample Z report end-to-end** — generated programmatically via `PosTransactionViewSet.zreport` (read-only, `force_authenticate` as `admin`):

`GET /api/transactions/zreport/?date=2026-05-15` (chosen: has SC + PWD discounts and two cashiers):
```json
{
  "date": "2026-05-15",
  "generated_at": "2026-05-16T14:03:05.666245+00:00",
  "generated_by": "admin",
  "transaction_count": 8,
  "total_items_sold": 10,
  "gross_sales": 1004.0,
  "void_count": 0,
  "void_total": 0.0,
  "net_sales": 1004.0,
  "vat_rate": null,
  "vat_inclusive": null,
  "vat_amount": 0.0,
  "vat_exempt_sales": 584.0,
  "net_of_vat": 1004.0,
  "average_transaction": 125.5,
  "cash_expected": 642.0,
  "first_txn_time": "2026-05-15T10:35:56.463361+00:00",
  "last_txn_time": "2026-05-15T12:41:48.170152+00:00",
  "opening_txn_no": "OR-20260515-0001",
  "closing_txn_no": "OR-20260515-0008",
  "busiest_hour": 19,
  "by_method": [
    { "payment_method": "cash",  "count": 5, "subtotal": 642.0 },
    { "payment_method": "gcash", "count": 2, "subtotal": 282.0 },
    { "payment_method": "maya",  "count": 1, "subtotal": 80.0 }
  ],
  "by_cashier": [
    { "name": "Ralph", "count": 5, "subtotal": 584.0, "void_count": 0 },
    { "name": "admin", "count": 3, "subtotal": 420.0, "void_count": 0 }
  ],
  "top_items": [
    { "name": "Blueberry Muffin",   "units_sold": 3, "revenue": 270.0 },
    { "name": "BLT Sandwich",       "units_sold": 3, "revenue": 480.0 },
    { "name": "Banana Bread",       "units_sold": 2, "revenue": 160.0 },
    { "name": "Brewed Coffee",      "units_sold": 1, "revenue": 80.0 },
    { "name": "Beef Salpicao Rice", "units_sold": 1, "revenue": 160.0 }
  ],
  "void_list": [],
  "discount_breakdown": [
    { "type": "pwd", "label": "PWD",             "count": 4, "total_discount": 114.0 },
    { "type": "sc",  "label": "Senior Citizen",  "count": 1, "total_discount": 32.0 }
  ],
  "total_discounts_given": 146.0
}
```
Cross-check against raw rows for 2026-05-15: `Sum(PosTransactionItem.subtotal)=1150` (true pre-discount gross) vs reported `gross_sales=1004`; `Sum(discount_amount)=146`; the 5 SC/PWD rows all have `vat_exempt=False, vat_amount=0.00` while `vat_exempt_sales` reports `584.0`. Discount breakdown (`pwd 4/₱114`, `sc 1/₱32`, total `146`) is internally accurate.

A second capture, `?date=2026-05-16` (8 txns, ₱1685, single cashier "Ana Cruz", no discounts/voids, `busiest_hour:1`, `first_txn_time` 2026-05-15T16:53Z = 2026-05-16 00:53 PHT — PHT grouping is internally consistent), was also generated and matches the same structure; omitted here for brevity.

---

## 10. Proposed Tickets

Conservative scope — gaps identified, not designed. Severity: **S1** critical / **S2** significant / **S3** minor.

| ID | Name | Sev | Summary | Dependencies |
|---|---|---|---|---|
| **ISSUE-070** | Z "Gross Sales" is post-discount net, mislabeled | S1 | `gross_sales`/`net_sales` use `Sum(total_amount)` which is already net of discount + SC/PWD VAT removal (`views.py:381,454`; `services.py:262-299`). No true pre-discount gross is computed; discounts then shown again separately. | Independent |
| **ISSUE-071** | Z VAT-exempt derived from `discount_type`, not `vat_exempt` flag | S2 | `vat_exempt_sales` (`views.py:391-394`) ignores `PosTransaction.vat_exempt`/`vat_amount`; divergent source of truth, value present even when VAT disabled. | Independent |
| **ISSUE-072** | Historical Z mutable; later void rewrites a past day's Z | S1 | Z is a stateless recompute keyed by `created_at__date`; a void (any later date) of an old sale retroactively alters that day's gross/voids. No snapshot. | Relates FLAG-074 |
| **ISSUE-073** | `cash_expected` excludes opening float; Over/Short unreconcilable | S2 | `cash_expected` = cash sales only (`views.py:467`); `Shift.opening_cash` never added; Z not shift-bound. | Relates FLAG-075 |
| **ISSUE-074** | Currency hardcoded `PHP`/`₱` despite configurable `BusinessProfile.currency` | S3 | `services.py:282`, `zreport.html:337`, `receipt_service.py`. | Independent |
| **ISSUE-075** | HTML Z report sources business name from DOM, omits TIN | S3 | `zreport.html:405-406` reads `#site-name`; no API-sourced biz name/TIN on screen (ESC/POS does print them). | Independent |
| **FLAG-072** | No Z counter / reset counter / accumulated grand total | S1 | No fields/computation for Z#, reset#, or since-installation grand total (`models.py:719-752`). | Independent |
| **FLAG-073** | No machine identity on reports (MIN / serial / accreditation) | S2 | Absent from `BusinessProfile` and reports. | Independent |
| **FLAG-074** | Z reports never persisted (no archive / retention) | S1 | No `ZReport` model; closing a shift does not snapshot (`views.py:1407-1442`). | Blocks ISSUE-072 fix |
| **FLAG-075** | Z report decoupled from `Shift`; close-shift does not finalize | S2 | Z keyed by calendar date, not shift; multiple shifts/day merged; close is a no-op for the report. | Relates FLAG-074 |
| **FLAG-076** | OR series not installation-wide sequential; seed bypass | S2 | Per-PHT-day reset counter (`models.py:462-476`); 346 seed txns bypass it (counter=29 for 368 txns). | Relates FLAG-047 |
| **FLAG-077** | No remote-owner delivery path (Tailscale-only, no viewer role, no export/digest) | S2 | No public access, no read-only role (`permissions.py:14-22`), no email/PDF/CSV/cron. | Independent |
| **FLAG-078** | Seed/real data indistinguishable in all aggregates | S2 | `is_seed` not shipped; Dashboard/all-time/month totals include March seed. | **FLAG-047** |
| **FEATURE-013** | Multi-day / period reporting for asynchronous owner review | S2 | No weekly/monthly rollup, top-N over a range, period comparison, or trend (gap only; design TBD by Ralph). | Builds on existing data; #6/#9 sub-gaps need **FEATURE-009** |
| **FEATURE-014** | Owner insight surface (peak DOW×hour, per-cashier trend, voids-per-cashier trend, discount trend, anomalies) | S2 | 7/10 Phase-5 asks are UI/query gaps over existing data; identify-only. | Margin/reorder slices need **FEATURE-009** (AUDIT-001) |
| **FEATURE-015** | Split / multi-payment-method transactions | S3 | `payment_method` single CharField (`models.py:308-313`); split tenders unrepresentable; payment breakdown can't reflect them. | Independent |
| **FEATURE-016** | Refund accounting distinct from void | S3 | `'refunded'` referenced (`views.py:239`) but no status/flow/report line. | Independent |

Cross-refs: COGS margin / reorder (Phase 5 #6, #9) and ingredient low-stock (#3 ingredient slice) are blocked by **FEATURE-009** and recipe coverage per AUDIT-001. `ItemLog` has no `sale` rows per **ISSUE-069**/AUDIT-001 — reports don't use `ItemLog`, but no independent reconciliation trail exists. **FLAG-047** (`is_seed`) is a hard prerequisite for any trustworthy multi-day/all-time number (FLAG-078, FEATURE-013).

---

## 11. Open Observations for Design Discussion

- **"Z" is a label, not a Z.** It performs no read/reset cycle, increments no counter, freezes nothing. Re-querying a past date can return different numbers than the first time it was viewed (after a later void or even a backfilled sale). Decide whether TarsierPOS needs a true finalize-and-freeze Z, or an immutable daily snapshot, or both.
- **Shift vs day mismatch.** X is per-open-shift-per-cashier; Z is per-calendar-day across all cashiers/shifts. An owner reasoning "per shift" and a Z reasoning "per day" will not reconcile when there are multiple shifts/day. Closing a shift produces no shift-level financial record.
- **No idempotency / no audit of Z generation.** Z can be generated unlimited times by any manager/admin with no log of who ran it or when it was "the" Z for the day.
- **Two Z renderings disagree on content.** The ESC/POS Z (`receipt_service.py:185-257`) omits VAT, top items, busiest hour, per-cashier, first/last time, and OR range that the HTML Z shows. "The Z report" is ambiguous.
- **"Gross" semantics will surprise an accountant.** The screen says Gross ₱1004 and separately Discounts ₱146 for the same day where true gross is ₱1150. Any BIR-style reading expects Gross − Discounts = Net; here Gross is already Net.
- **VAT is off in the live profile**, so the entire VAT card currently renders zeros — the VAT logic is effectively untested against live data and only exercised by the SC/PWD path in code.
- **Remote owner ergonomics:** no read-only role means handing the owner manager/admin credentials (full write) just to read a report; Tailscale-on-phone is a real onboarding burden for a non-technical café owner; report HTML scales but is not phone-first.
- **Seed contamination is systemic for any "trend" feature.** Until FLAG-047 ships, every all-time/month/trend number (Dashboard included) is fiction-diluted by ~346 March seed rows ringing at 00:00–01:00.
- **`dashboard.html` mislabels month as "today".** `dashboard.html:342-349` assigns `data.month.revenue`/`data.month.transactions` to elements `#today-sales`/`#today-transactions`. Minor UI defect, but it means the existing "dashboard" the owner might rely on is itself untrustworthy at face value — worth confirming during design.
- **Cash reconciliation is structurally open:** no opening float in `cash_expected`, Over/Short is a hand-written blank line, `Shift.closing_cash` is captured but never flowed into the Z. The cash story needs an end-to-end design, not a field tweak.
