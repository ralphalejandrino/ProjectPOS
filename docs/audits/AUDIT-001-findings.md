# AUDIT-001 — Item ↔ Ingredient Stock Linkage Investigation

**Auditor:** Claude (automated audit)
**Date:** 2026-05-16
**Repo:** `/home/ralph/TarsierPOS`
**Scope:** Audit only. No code, migrations, or data were modified. Nothing committed or pushed.

---

## 1. TL;DR

Ralph's *mechanistic* hypothesis — "there is no recipe/BOM mechanism, the divergence is structural" — is **REFUTED**. A recipe/BOM model (`RecipeIngredient`) exists, and the live POS sale path **does** decrement ingredient stock when an item is sold (`services.py:340`). The earlier FLAG-026 closure ("no recipe builder or frontend/src exists") was **factually wrong**: there is no `frontend/src`, but a working recipe builder exists in `frontend/public/ingredients.html` and the depletion code is fully wired.

However, Ralph's *business-level* concern — "the business is losing visibility into ingredient consumption" — is **CONFIRMED**, for different reasons than suspected:

1. **There is no ingredient-consumption ledger at all.** Depletion is a bare `current_stock = F('current_stock') - x` UPDATE with no log row. `IngredientRestockLog` only records *additions*. There is no `IngredientLog`/`StockMovement`. Consumption is therefore invisible and unauditable — proven below: 41 of 42 ingredients cannot be reconciled because no movement ledger and no initial-stock baseline exist.
2. **Recipe coverage is partial.** 34 of 70 active items have zero recipe rows. 5 items sold in the last 30 days (Beef Salpicao Rice, Brewed Coffee, Buko Pandan Salad, Butter Croissant, Cafe Latte) deplete **nothing** on every sale — a silent gap, not an error.
3. **Sales are never written to `ItemLog`.** Even the Item side has no per-sale audit trail (`ItemLog` rows with `action='sale'` = 0).
4. **The variant-recipe path is effectively dead UI** and would raise an IntegrityError if it weren't.
5. **Seed/demo transactions bypass the service entirely**, so 346 of 368 historical transactions depleted no ingredients — historical ingredient figures are fiction.

**Bottom line for Gio:** When a coffee is sold through the live POS today, *if that coffee has a recipe configured*, the beans/milk/syrup amounts are subtracted from ingredient stock. But (a) ~half the menu has no recipe, so those sales consume invisibly; (b) there is no log of what was consumed, so the ingredient numbers cannot be trusted, audited, or reconciled; (c) manual Item adjustments and ingredient edits are independent and untracked. The business has *partial, unverifiable* visibility into ingredient consumption — which for inventory-control purposes is equivalent to no visibility.

---

## 2. Data Model State

### Models in scope (`canteen/models.py`)

| Model | Lines | Role |
|---|---|---|
| `Item` | 128–200 | Product for sale. `stock` = `PositiveIntegerField` (157). `save()` override (190) only generates barcode image. No ingredient link on the model itself. |
| `ItemLog` | 203–240 | Item stock-change ledger. FK `item` (related_name `logs`), `quantity` (signed), `current_stock`, `action` choices include `'sale'` (220–225). |
| `ItemCategory` | 54–66 | Category. |
| `VariantGroup` / `VariantOption` | 69–100 | Variant system. `VariantOption` (88) is the variant entity referenced by recipes. **There is no `ProductVariant` model** (the comment at 523–529 references a hypothetical future one). |
| `CategoryVariantGroup` / `ProductVariantGroup` | 103–125 | Variant group assignment/overrides. |
| `PosTransaction` | 291–476 | Sale. `status` ∈ completed/pending/void (294–298), `void` bool (271), `voided_at` (442). |
| `PosTransactionItem` | 479–520 | Line item. FK `item` `on_delete=PROTECT` (487), `quantity` (488). No ingredient snapshot. |
| `TransactionItemVariant` | 523–539 | Variant snapshot on a line item — stores `group_name`/`option_name` as **plain CharFields** (no FK back to `VariantOption`). |
| `Supplier` | 801–814 | Ingredient supplier. |
| `IngredientUnit` | 788–798 | Unit of measure (`abbreviation`). |
| `Ingredient` | 817–836 | `current_stock` = `DecimalField(max_digits=10, decimal_places=4, default=0)` (822) — **no non-negative validator; can go negative**. `unit` FK → `IngredientUnit` (820). No `last_modified`/`updated_at` field. |
| `IngredientRestockLog` | 839–861 | Ingredient **additions only**. `save()` override (851–858) increments `ingredient.current_stock` on insert. No depletion counterpart. |
| `RecipeIngredient` | 864–883 | **THE LINK.** See below. |

### The Item ↔ Ingredient link — IT EXISTS

`RecipeIngredient` (`canteen/models.py:864–883`):

- `item` → FK `Item`, nullable, `related_name='recipe_ingredients'` (866)
- `variant` → FK **`VariantOption`**, nullable, `related_name='recipe_ingredients'` (867)
- `ingredient` → FK `Ingredient`, `related_name='recipes'` (868)
- `quantity_used` = `DecimalField(max_digits=10, decimal_places=4)` (869) — the "N units of this ingredient per item" amount
- **Unit field:** none on `RecipeIngredient`; the unit is implied by `Ingredient.unit` (`models.py:820`). `quantity_used` is assumed to be in the ingredient's own unit. No unit conversion anywhere.
- `CheckConstraint` `recipe_item_or_variant_not_both` (872–880): exactly one of `item`/`variant` must be set.

**Direction/shape:** `Item` 1—N `RecipeIngredient` N—1 `Ingredient` (a through-model BOM; quantity on the through row). Variant-level recipes attach to `VariantOption` instead of `Item`.

**Conclusion:** A recipe/BOM mechanism **exists in the data model**. The divergence Ralph observes is **not structural at the model layer**. (Live data: 103 `RecipeIngredient` rows across 42 ingredients.)

---

## 3. Transaction-Time Behavior

### Sale completion — `create_pos_transaction()` (`canteen/services.py:85–345`)

There is exactly one service-layer completion path: `create_pos_transaction`. It is called from `views.py:195, 849, 911, 969` (POS checkout endpoints). Everything runs in `db_transaction.atomic()` (services.py:90), items are `select_for_update()` locked (services.py:106).

On commit, per line item (services.py:302–340):

| Mutation | Happens? | Where |
|---|---|---|
| `PosTransaction` row created | Yes | `services.py:281` |
| `PosTransactionItem` rows created | Yes | `services.py:304` |
| `TransactionItemVariant` snapshots | Yes | `services.py:317` |
| `Item.stock` decremented | Yes — atomic `UPDATE … WHERE stock>=qty` | `services.py:326–329` |
| **`ItemLog` row created (`action='sale'`)** | **NO** | services.py imports no `ItemLog`; none created. Live: `ItemLog` rows with `action='sale'` = **0**. |
| `Ingredient.current_stock` depleted | Yes — `_deplete_ingredients()` | call `services.py:340`, impl `services.py:17–45` |
| **Ingredient depletion log row** | **NO** | `_deplete_ingredients` does a bare `F('current_stock') - qty` UPDATE (services.py:32–34, 43–45). No log model written. |

`_deplete_ingredients` (`services.py:17–45`): variant recipes (`RecipeIngredient.variant_id IN selected option ids`) take priority; item recipes (`RecipeIngredient.item=item`) fill the rest; `current_stock = F('current_stock') - quantity_used*qty`. **Silently returns if no recipe is configured** (docstring says so explicitly, services.py:22). No floor at zero — stock can go negative.

### `track_inventory` flag — actual effect

`BusinessProfile.track_inventory` (`models.py:738`, default `True`). In the sale path it is read once (`services.py:95`) and gates a single block:

```
services.py:324   if _track_inventory:
services.py:326–329    Item.stock decrement
services.py:340        _deplete_ingredients(...)
```

- `track_inventory=True` → Item stock decremented **and** ingredients depleted.
- `track_inventory=False` → **both** are skipped. Stock validation up front (`services.py:108`) is also skipped.

**Finding:** Ingredient depletion is *coupled to the Item inventory flag*. A business that disables Item tracking (e.g., sells untracked retail) **also silently stops all ingredient depletion**, even though the two are conceptually independent. There is no separate "track ingredients" switch.

### Void / reversal path (`canteen/views.py:228–273`)

Gated by `if not _bp or _bp.track_inventory:` (views.py:245). Per line item:

| Mutation | Happens? | Where |
|---|---|---|
| `Item.stock` restored (`+qty`) | Yes | `views.py:247–249` |
| `Ingredient.current_stock` restored | Yes — `_restore_ingredients()` | call `views.py:251`, impl `services.py:48–82` |
| `ItemLog` row created (`action='return'`) | **Yes** | `views.py:253–259` |
| **Ingredient restore log row** | **NO** | `_restore_ingredients` is bare `F('current_stock') + x` (services.py:69–71, 80–82) |

**Void asymmetry / fragility:**
- The sale path writes **no** `ItemLog`; the void path writes an `ItemLog` `action='return'`. So `ItemLog` can show a return with no corresponding sale row — the ledger is one-sided.
- `_restore_ingredients` (services.py:56–61) resolves variant recipes by `VariantOption.objects.filter(name__in=<option_name snapshots>)` — a **global name match**, not scoped to the item or variant group. If an option was renamed/deleted after the sale, or two groups share an option name (e.g. "Large"), the restore matches the wrong recipe rows or none — **silently** (docstring: "Silently skips if no recipe is configured").
- If `track_inventory` was `False` at sale time (no depletion) but `True` at void time, the void will **add** ingredient stock that was never subtracted — phantom inflation. (And vice-versa.)

### Variant handling at the stock layer

- The model supports variant-specific recipes (`RecipeIngredient.variant` → `VariantOption`).
- `_deplete_ingredients` does consume variant recipes when `variant_option_ids` are passed (`services.py:27–35`), and they take priority over the base item recipe.
- **But the variant-recipe authoring path is effectively dead** (see §4 / Manual behavior): the recipe-builder UI's variant dropdown is fed by a non-existent endpoint, so variant-scoped `RecipeIngredient` rows are never created in practice. Net effect: selling "Large Latte" vs "Latte" consumes the **same** base-item recipe (or nothing). Variant size/add-on differences are **ignored at the stock layer** in current data.

---

## 4. Manual Adjustment Behavior

### `Item.adjust_stock` (`canteen/views.py:1234–1280`)

- Operates on **Items only** (`Item.objects.select_for_update().get(pk=pk)`, views.py:1250).
- Payload: `{ "adjustment": <int signed>, "reason": <str> }` (views.py:1240–1241). Bounds: |adj| ≤ 10000, resulting stock 0…999999.
- **Writes an `ItemLog`** `action='adjustment'` (views.py:1267–1273). Good — Item manual adjustments *are* logged.
- **Does NOT touch ingredients.** Manually correcting an Item's count does not reconcile its constituent ingredients (and vice-versa).
- Frontend: `frontend/public/inventory.html:664` POSTs to `/items/{id}/adjust_stock/`. `inventory.html` calls **only** `/items*` endpoints — never an ingredient endpoint.

### Ingredient manual adjustment

- **No dedicated ingredient `adjust_stock` endpoint exists.** The only structured ingredient-stock mutation is `IngredientViewSet.restock` (`views.py:1556–1563`, URL `/ingredients/{id}/restock/`), which creates an `IngredientRestockLog`; the model's `save()` (models.py:851–858) increments `current_stock`. Additions only — logged.
- **However `IngredientViewSet` is a full `ModelViewSet`** (`views.py:1551`) and `IngredientSerializer` exposes `current_stock` as a **writable** field (`canteen/serializers.py:499–511`, `current_stock` in `fields`, not in any `read_only_fields`). So a manager can `PATCH /ingredients/{id}/` and set `current_stock` to any value **with no log row whatsoever**. This is a silent, untracked write path.
- `frontend/public/ingredients.html` calls `/ingredients*` and `/recipe-ingredients*` and reads `/items/` (for the recipe builder's item dropdown, line 865) and a **non-existent** `/items/{id}/variants/` (line 898, error swallowed by `.catch(()=>null)`, line 898). It does not POST to any Item stock endpoint.

### Can admin/manager edit the two sides independently? — Yes.

| Side | UI | Endpoint | Logged? |
|---|---|---|---|
| Item stock | inventory.html | `POST /items/{id}/adjust_stock/` | Yes (`ItemLog`) |
| Ingredient stock (add) | ingredients.html | `POST /ingredients/{id}/restock/` | Yes (`IngredientRestockLog`) |
| Ingredient stock (set/overwrite) | ingredients.html edit form | `PATCH /ingredients/{id}/` (`current_stock` writable) | **No log** |
| Ingredient depletion from sale | POS | `_deplete_ingredients` | **No log** |

Two independent stock systems, asymmetric audit coverage, no cross-reconciliation. The recipe builder itself works for **item-level** recipes (`ingredients.html:966–988` POSTs `{item, ingredient, quantity_used}` to `/recipe-ingredients/`) but its variant feature is broken: the variant `<select>` is populated from `/items/{id}/variants/` which **is not a registered route** (no `variants` action in `ItemViewSet`; confirmed absent in `views.py`), so it is always empty; and `saveRecipeIngredient()` (`ingredients.html:973–975`) sends **both** `item` and `variant` when a variant is chosen, which would violate the `recipe_item_or_variant_not_both` CheckConstraint (IntegrityError) if it were ever reachable.

---

## 5. Live Data Evidence

`source venv/bin/activate && python manage.py shell` (read-only queries). `BusinessProfile.track_inventory = True`.

**Aggregate counts:** Items 70 · Ingredients 42 · `RecipeIngredient` rows 103 · `PosTransaction` 368 (completed last-30d: **22**; older than 30d: **346**; void: 4) · `ItemLog` rows total **3** · **`ItemLog` `action='sale'` = 0** · `IngredientRestockLog` 85.

**Top items sold, last 30 days** (`stockNow` / recipe rows / last sale):

| Item | Sales | Qty sold | Stock now | Recipe rows | Last sale |
|---|---|---|---|---|---|
| BLT Sandwich | 8 | 8 | 92 | 4 | 2026-05-15 |
| Belgian Waffle | 2 | 5 | 95 | 2 | 2026-05-15 |
| Banana Bread | 3 | 4 | 96 | 4 | 2026-05-16 |
| Beef Salpicao Rice | 4 | 4 | 95 | **0** | 2026-05-15 |
| Brewed Coffee | 3 | 3 | 92 | **0** | 2026-05-15 |
| Blueberry Muffin | 3 | 3 | 97 | 4 | 2026-05-15 |
| Buko Pandan Salad | 2 | 2 | 98 | **0** | 2026-05-14 |
| Butter Croissant | 1 | 1 | 99 | **0** | 2026-05-14 |
| Cafe Latte | 1 | 1 | 99 | **0** | 2026-05-14 |

Item `stock` is decrementing correctly (BLT: 100→92 after 8 sold). **34 of 70 active items have no recipe rows.** **5 items sold in the last 30 days deplete zero ingredients on every sale:** Beef Salpicao Rice, Brewed Coffee, Buko Pandan Salad, Butter Croissant, Cafe Latte.

**Reconciliation attempt (expected vs. observed ingredient stock).** Because there is no consumption ledger and no recorded initial-stock baseline, the only computable model is `expected = Σ restock_additions − Σ sale_depletion + Σ void_restore`. Result: **41 of 42 ingredients fail to reconcile**; expected values are massively negative (e.g. Whole Milk: current 25000, expected −64670, drift 89670). The single zero-drift ingredient (Oat Milk) is used in 0 recipes.

This is **not** evidence the live deplete code is broken — it is evidence of three things, all of which support the visibility finding:
1. **346/368 transactions are seed/demo data** created by `seed_demo._seed_transactions` (`canteen/management/commands/seed_demo.py:669+`), which builds `PosTransaction(...)` / `PosTransactionItem.objects.create(...)` **directly and never calls `create_pos_transaction` or `_deplete_ingredients`**. Those historical sales depleted nothing; ingredient stock was seeded independently via `IngredientRestockLog` (seed_demo.py:637). Historical ingredient levels are therefore fictitious.
2. **There is no movement ledger and no initial-stock field**, so even legitimate live depletion cannot be verified or reconciled after the fact. The drift is *uncomputable by construction* — which is exactly the visibility gap.
3. Round, positive `current_stock` values (25000, 5000, 1500, …) across nearly all ingredients confirm stock is being *set* by seeding/direct edits rather than *evolved* by logged movements.

```
NO LINKAGE — DIVERGENCE CANNOT BE CALCULATED, BUT GUARANTEED
```
(Stated as required by Phase 4.5. Interpretation: a recipe linkage *does* exist in code, but because there is **no ingredient-movement ledger and no initial-stock baseline**, divergence cannot be calculated — and given partial recipe coverage + seed bypass + untracked manual edits, divergence is guaranteed in practice.)

**One-line evidence summary:** 22 live sales in 30 days across a 70-item menu where 34 items (incl. 5 actively sold) have no recipe and ingredient consumption has zero audit rows — ingredient figures are unverifiable and partially fictional.

---

## 6. Proposed Tickets

Prefixes follow existing convention. Severity: BLOCKER / High / Medium / Low. Ralph assigns numbers.

| Proposed name | Type | Severity | Depends on | Rationale (file:line) |
|---|---|---|---|---|
| **No ingredient consumption ledger (depletion is unauditable)** | FLAG | **BLOCKER** | — | `_deplete_ingredients` / `_restore_ingredients` do bare `F()` updates with no log row (`services.py:32–34,43–45,69–71,80–82`). No `IngredientLog`/`StockMovement` model exists. Makes all ingredient figures unverifiable. |
| **`IngredientLog` / `StockMovement` model + wire all ingredient mutations through it** | FEATURE | **BLOCKER** | depends-on: nothing; blocks the ledger FLAG above being closeable | New append-only ledger capturing sale-depletion, void-restore, restock, and manual set; mirror of `ItemLog` for the ingredient side. |
| **Sales never written to `ItemLog` (`action='sale'`)** | ISSUE | High | — | `create_pos_transaction` decrements `Item.stock` (`services.py:326–329`) but writes no `ItemLog`; live `ItemLog action='sale'` = 0. Item ledger is one-sided (void writes 'return', sale writes nothing — `views.py:253`). |
| **Reopen/supersede FLAG-026 — closure rationale is factually wrong** | FLAG | High | — | FLAG-026 closed as "no recipe builder or frontend/src exists." Recipe builder exists at `frontend/public/ingredients.html:862–997`; depletion wired at `services.py:340`. `frontend/src` never existed (app is static `frontend/public/`). |
| **Recipe/BOM coverage gap — 34/70 active items have no recipe** | FLAG | High | New `IngredientLog` (to measure), recipe-coverage report | 34 items, incl. 5 sold last 30d (Beef Salpicao Rice, Brewed Coffee, Buko Pandan Salad, Butter Croissant, Cafe Latte) deplete nothing on sale. `_deplete_ingredients` silently no-ops (`services.py:22`). |
| **Variant-recipe authoring path is dead UI + would violate CheckConstraint** | ISSUE | High | — | `ingredients.html:898` fetches non-existent `/items/{id}/variants/` (no `variants` action in `ItemViewSet`); error swallowed (`.catch(()=>null)`). `saveRecipeIngredient` sends both `item` and `variant` (`ingredients.html:973–975`) → violates `recipe_item_or_variant_not_both` (`models.py:872–880`). Variant differences ignored at stock layer. |
| **Ingredient `current_stock` is silently writable via `PATCH /ingredients/{id}/`** | ISSUE | High | New `IngredientLog` (for the fix) | `IngredientViewSet` is full `ModelViewSet` (`views.py:1551`); `current_stock` in writable serializer fields with no `read_only` (`serializers.py:506–511`). Untracked overwrite path. |
| **Ingredient depletion coupled to `track_inventory` (no independent ingredient switch)** | FLAG | Medium | — | Depletion nested in `if _track_inventory:` (`services.py:324→340`); disabling Item tracking silently stops ingredient depletion. Same coupling in void (`views.py:245`). |
| **Void ingredient-restore matches variants by global name (fragile/silent)** | ISSUE | Medium | New `IngredientLog` (to detect future misfires) | `_restore_ingredients` resolves via `VariantOption.objects.filter(name__in=…snapshot…)` (`services.py:56–61`) — not scoped to item/group; rename/delete/duplicate-name → wrong or missed restore, silent. |
| **Seed/demo transactions bypass `create_pos_transaction` (no depletion)** | FLAG | Medium | — | `seed_demo._seed_transactions` builds `PosTransaction`/`PosTransactionItem` directly (`canteen/management/commands/seed_demo.py:669+`, `848`, `873`); historical ingredient levels are fictional. Document and/or route seed through the service. |
| **`Ingredient` lacks `updated_at`/`last_modified`; `current_stock` has no non-negative validator** | FLAG | Low | — | `models.py:817–836`: no timestamp field (Phase 4 could not report `last_modified`); `current_stock` `DecimalField` with no `validate_non_negative_*` — depletion can drive it negative silently. |

**Suggested dependency ordering:** the `IngredientLog`/`StockMovement` FEATURE is the keystone — it unblocks closing the "no ledger" BLOCKER, the silent-`PATCH` ISSUE, the void-restore ISSUE, and makes the coverage-gap FLAG measurable. Recommend it first, then the `ItemLog` sale-logging ISSUE (symmetry), then coverage + variant-authoring fixes.

---

## Notes & Uncertainties

- **`/items/{id}/variants/`**: I confirmed no `variants` @action in `ItemViewSet` and no matching route in `urls.py`. If such a route is added elsewhere (e.g., dynamically) the "dead UI" finding for variant recipes would soften to "no variant recipes currently authored" — but the live-data fact (0 variant-scoped recipes exercised, 34 items uncovered) stands either way.
- **Reconciliation drift**: the large numbers are an artifact of (no baseline + seed bypass + counting all 368 txns), *not* proof the live deplete code miscomputes. The defensible claim is narrower and stronger: *consumption cannot be audited or reconciled at all*, which is the real risk.
- I did not run `makemigrations`/`migrate`; all shell work was read-only `SELECT`-equivalent ORM queries. No files modified, nothing committed or pushed.
