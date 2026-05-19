# AUDIT-002 — Variant System End-to-End Investigation

**Date:** 2026-05-16
**Repo:** `/home/ralph/TarsierPOS`
**Scope:** Read-only audit. No code/migrations/commits.
**Builds on:** AUDIT-001 (recipe model existence, sale-path depletion call site, IngredientLog absence — not re-investigated here).

---

## 1. TL;DR

**Variants are functional — not receipt-only decoration.** Contrary to the headline suspicion, a real `price_modifier` field exists on `VariantOption` (`models.py:92`), it **is** summed and applied server-side at checkout (`services.py:186`, `194`), it **is** persisted to `PosTransactionItem.final_price`/`subtotal` and snapshotted to `TransactionItemVariant.price_modifier` (`services.py:308–322`), and it **is** rendered on the receipt (`receipt_service.py:78–80`). Live data confirms it: across the last 30 days every line item's `final_price − base_price` exactly equals the sum of its variant modifiers (28/28 consistent, 0 inconsistent), and ₱105 of variant modifiers were correctly collected. **There is no price-collection gap.** Selecting "Large" charges +₱20.

The real problems are narrower but genuine:

1. **Required-selection enforcement is bypassable** (`services.py:117`): the entire validation block — including the "required group must have a selection" check — only runs `if variant_selections:`. A client that omits the `variant_selections` key entirely sells the item at base price with no validation. **Currently latent** because all 6 live groups have `is_required=False`, so the moment anyone marks a group required, it is silently unenforced server-side for empty payloads.
2. **Variant-scoped depletion remains dead** (reconfirms AUDIT-001 ISSUE-070): 0 of 103 `RecipeIngredient` rows are variant-scoped. No variant consumes ingredients today.
3. **Snapshot drift is currently zero but the collision hazard is real** (AUDIT-001 ISSUE-072): 0 of 2,483 `TransactionItemVariant` rows are orphaned today, but option names collide across groups in live data ("Regular" ×3, "Hot" ×2) and `_restore_ingredients` matches options by **name only, not group** (`services.py:57–61`) — a wrong-recipe restore waiting for ISSUE-070 to be fixed.
4. **Two independent implementations of group-resolution** (`serializers.py:114–135` vs `services.py:121–159`) that currently agree but will drift.

Customer/Gio-facing impact: **revenue at risk is low today** (modifiers are collected; required groups not yet used). **Data-integrity risk is latent-high**: enabling required groups or authoring variant recipes activates the enforcement-bypass and the void-restore-collision bugs respectively. **Ops visibility** is fine for pricing, absent for variant-level ingredient consumption.

---

## 2. Variant Model State

All variant models live in `canteen/models.py`. None of `VariantGroup`, `VariantOption`, `CategoryVariantGroup`, `ProductVariantGroup`, `TransactionItemVariant` use the `BaseModelWithUUID` base except where noted; they declare their own `id`.

### `VariantGroup` (`models.py:69–85`)
| Field | Type | Params/Default |
|---|---|---|
| `id` | UUIDField | PK, `default=uuid.uuid4`, `editable=False` |
| `name` | CharField | `max_length=100` |
| `selection_type` | CharField | `max_length=10`, choices `single`/`multi`, `default='single'` |
| `is_required` | BooleanField | `default=False` |
| `sort_order` | PositiveSmallIntegerField | `default=0` |
| `is_active` | BooleanField | `default=True` |

- FKs: none.
- `Meta`: `ordering = ['sort_order', 'name']`. **No constraints, no indexes, no `unique` on `name`.**
- No `clean()` / `save()` override.

### `VariantOption` (`models.py:88–100`)
| Field | Type | Params/Default |
|---|---|---|
| `id` | UUIDField | PK, `default=uuid.uuid4`, `editable=False` |
| `group` | FK → `VariantGroup` | `on_delete=CASCADE`, `related_name='options'`, NOT NULL |
| `name` | CharField | `max_length=100` |
| **`price_modifier`** | **DecimalField** | **`max_digits=8`, `decimal_places=2`, `default=0`** |
| `sort_order` | PositiveSmallIntegerField | `default=0` |
| `is_active` | BooleanField | `default=True` |

- `Meta`: `ordering = ['sort_order', 'name']`. **No `unique_together` on `(group, name)`** → duplicate option names allowed within a group and across groups.
- No `clean()` / `save()` override.
- **Price-modifier field: PRESENT.** `price_modifier` is exactly the price-delta field. The AUDIT-002 headline suspicion ("no variant-level price modifier exists") is **FALSE**. Negative values are storable (signed DecimalField, no validator).

### `CategoryVariantGroup` (`models.py:103–112`)
| Field | Type | Params/Default |
|---|---|---|
| `id` | (implicit AutoField PK) | — |
| `category` | FK → `ItemCategory` | `on_delete=CASCADE`, `related_name='variant_groups'` |
| `group` | FK → `VariantGroup` | `on_delete=CASCADE`, `related_name='category_assignments'` |
| `is_required_override` | BooleanField | `null=True, blank=True` (tri-state: True/False/None) |

- `Meta`: `unique_together = [('category', 'group')]`. No `clean()`/`save()`.

### `ProductVariantGroup` (`models.py:115–125`)
| Field | Type | Params/Default |
|---|---|---|
| `id` | (implicit AutoField PK) | — |
| `product` | FK → `Item` | `on_delete=CASCADE`, `related_name='variant_group_overrides'` |
| `group` | FK → `VariantGroup` | `on_delete=CASCADE`, `related_name='product_overrides'` |
| `enabled` | BooleanField | `default=True` |
| `is_required_override` | BooleanField | `null=True, blank=True` |

- `Meta`: `unique_together = [('product', 'group')]`. No `clean()`/`save()`.

### `TransactionItemVariant` (`models.py:523–539`)
| Field | Type | Params/Default |
|---|---|---|
| `id` | UUIDField | PK, `default=uuid.uuid4`, `editable=False` |
| `transaction_item` | FK → `PosTransactionItem` | `on_delete=CASCADE`, `related_name='variant_selections'` |
| `group_name` | CharField | `max_length=100` (snapshot string, **no FK**) |
| `option_name` | CharField | `max_length=100` (snapshot string, **no FK**) |
| `price_modifier` | DecimalField | `max_digits=8`, `decimal_places=2`, **NOT NULL, no default** |

- `Meta`: none. No `clean()`/`save()`. In-code comment (`models.py:524–529`) acknowledges the no-FK design is intentional for receipt immutability (AUDIT-001 ISSUE-072).

### `PosTransactionItem` (price-relevant fields, `models.py:492–517`)
- `unit_price` (NOT NULL), `subtotal` (NOT NULL), `base_price` (`null=True, blank=True`), `final_price` (`null=True, blank=True`).
- `save()` override (`models.py:513–517`): auto-fills `subtotal = unit_price * quantity` **only if `subtotal is None`**. The service always passes `subtotal`, so this never fires on the POS path.

### `RecipeIngredient` (variant-relevant, `models.py:864–880`)
- `item` FK→Item (nullable), `variant` FK→`VariantOption` (`null=True, blank=True`, `on_delete=CASCADE`, `related_name='recipe_ingredients'`), `ingredient` FK→Ingredient, `quantity_used` Decimal.
- `Meta.constraints`: `CheckConstraint name='recipe_item_or_variant_not_both'` — exactly one of `item`/`variant` must be set. So variant recipes have `item IS NULL`.

> The headline suspicion is refuted: **a variant-level price modifier (`VariantOption.price_modifier`) exists and is the operative pricing field.**

---

## 3. Pricing Behavior

The single authoritative pricing path is `create_pos_transaction` (`services.py:85–346`). All four POS entry points route through it: cash (`views.py:195`), GCash (`views.py:849`), Maya (`views.py:911`), Card (`views.py:969`).

Trace for "customer selects Large":
1. `base_price = Decimal(str(item.price))` — `services.py:113`.
2. `modifier_total = Decimal('0.00')` — `services.py:114`.
3. For each validated selection: `modifier_total += Decimal(str(option.price_modifier))` — **`services.py:186`** (the modifier is consulted and summed).
4. `final_unit_price = base_price + modifier_total` — **`services.py:194`**.
5. `subtotal = final_unit_price * quantity` — `services.py:195`; rolled into `total` — `196`.
6. Persisted: `PosTransactionItem` created with `unit_price=final_unit_price`, `base_price`, `final_price` — `services.py:304–313`.
7. Snapshotted: one `TransactionItemVariant` per selection with `price_modifier=rv['option'].price_modifier` — `services.py:317–322`.

**The server is authoritative and recomputes price from the DB `item.price` + DB `option.price_modifier`.** The client also sends `price` (`app.js:681`, `752`) but the service ignores it — there is **no client/server price-trust gap**. Client-side preview math (`app.js:363–369`, `408`) mirrors the server formula, so the cart total shown equals what is charged.

**Dead code:** `PosTransactionCreateSerializer` (`serializers.py:263–304`) trusts client `unit_price`, marks `variant_selections` read-only, and never creates variant rows — but it is **referenced nowhere** (`grep` across `canteen/` finds only its definition). It is a latent footgun if ever wired up, but not a live risk.

Receipt (`receipt_service.py:78–80`): prints each variant as `  {group_name}: {option_name} {+PHP modifier}`. Line subtotal already includes the modifier (since `unit_price == final_price`). **Minor display issue:** negative modifiers are suppressed (`modifier_str` only set when `price_modifier > 0`), so a discount-style option would print with no price annotation.

---

## 4. Selection Enforcement

All enforcement is server-side in `create_pos_transaction`; there is **no serializer-layer validation** (the only transaction serializer that exists is the dead `PosTransactionCreateSerializer`). Frontend (`app.js:378–407`) duplicates the rules client-side for UX but is not the security boundary.

- **Required vs optional:** Enforced at `services.py:161–165` (`required_map[gid]` true and group not in selected → `DRFValidationError`). Precedence (`services.py:148–159`): global `VariantGroup.is_required` → `CategoryVariantGroup.is_required_override` → `ProductVariantGroup.is_required_override` (last non-None wins). **CRITICAL GAP:** this whole block is inside `if variant_selections:` (`services.py:117`). If the client sends an item with **no `variant_selections` key at all**, required enforcement (and option-validity checks) is entirely skipped — item sells at base price, no snapshot, no depletion. Latent today (all 6 live groups `is_required=False`) but a real bypass.
- **Cardinality:** `selection_type` (`single`/`multi`) on `VariantGroup`. Single-select enforced at `services.py:177–180` (a second selection for a `single` group → error). **`multi` groups have no `min_select`/`max_select` fields and no min/max enforcement** — a required `multi` group is satisfied by any one selection; there is no "choose at least N / at most M".
- **Defaults:** No default-option concept exists on `VariantOption`/`VariantGroup`. If the user picks nothing for an optional group, nothing is recorded (item priced at base).
- **Server-side validation of payload:** For each selection (`services.py:167–192`): group must be in the item's `effective_groups` (else error `172–174`); option must belong to that group and be `is_active` (`182–184`). So options-from-wrong-group and inactive options are rejected — **provided `variant_selections` is non-empty** (same `if` gate). A malformed payload that simply omits required groups reaches the DB unflagged.
- **Group attachment resolution:** Two relationships. `CategoryVariantGroup` attaches a group to all items in a category; `ProductVariantGroup` overrides per-item. Resolution is **addition + per-group enable/disable + required-override**, not wholesale replacement:
  - Authoritative-for-frontend: `ItemSerializer.get_effective_variant_groups` (`serializers.py:114–135`): start with category groups; a product override with `enabled=False` removes that group (`pop`, line 128); `enabled=True` adds/replaces it; `is_required` resolves override-or-group-default.
  - Re-implemented in the service: `services.py:121–159` builds the same effective set independently. The POS frontend gets its render list from the **Item list/retrieve endpoint** (`ItemViewSet` → `ItemSerializer`, `views.py:1192`; prefetch at `views.py:1166–1167`), consumed at `app.js:263, 326`.
  - **Finding:** the resolution algorithm is implemented twice (serializer vs service). They agree today but are not a shared function — high drift risk. Live data has 0 `ProductVariantGroup` rows, so the override path is currently untested in production.

---

## 5. Depletion Edge Cases

`_deplete_ingredients(item, variant_option_ids, quantity)` — `services.py:17–45`. `variant_option_ids` is the list of **all** selected option IDs across **all** groups for the line (`services.py:336–339`).

1. **Overlap (variant vs item, same ingredient):** Variant wins. Variant recipes processed first (`services.py:27–35`), each ingredient pk added to `depleted_ingredient_ids`; the item-recipe loop skips any pk already in that set (`services.py:42`). The variant quantity **replaces**, does not add to, the base quantity for that ingredient.
2. **Multi-group transactions (e.g. Size=Large AND Milk=Oat):** Both options' recipes are accumulated — `RecipeIngredient.objects.filter(variant_id__in=variant_option_ids)` (`services.py:28–29`) pulls recipes for **every** selected option, and each row triggers its own `F()` decrement (`services.py:31–34`). It does not pick one or fail.
3. **Additive "Large = base +50ml":** **Not expressible.** Because a variant recipe for an ingredient fully suppresses the base recipe for that ingredient (point 1), "Large" must restate the *full* required quantity of every ingredient it touches, not a delta. There is no additive/delta semantic in the model.
4. **Empty selections:** `variant_option_ids` empty → the `if variant_option_ids:` block is skipped → falls through to item-only recipe (`services.py:38–45`). Correct.
5. **Mixed coverage (variant specifies only milk; base specifies milk+espresso+cup):** Milk depleted via variant recipe and added to `depleted_ingredient_ids`; espresso and cup are not in that set so the item loop depletes them. All three deplete correctly.
6. **Conflict at variant level (two options from different groups, same ingredient, different qty):** **Both are applied (additive/sum), not first/last-wins.** `depleted_ingredient_ids.add()` runs *after* each variant decrement and only gates the *item-level* fallback — it never dedupes variant-vs-variant. So Size=Large (espresso 18g) + Additionals=Extra Shot (espresso 9g) → 27g deducted. This is plausibly the *intended* behavior for stackable add-ons, but it is **undocumented and silent**; for mutually-exclusive-looking options in different groups it could over-deplete. Flag as ambiguous-by-design.

**All of the above is theoretical today:** 0 of 103 `RecipeIngredient` rows are variant-scoped (reconfirms AUDIT-001 ISSUE-070). No variant depletion has ever executed in production.

Void/restore: `_restore_ingredients` (`services.py:48–82`) resolves options from snapshots via `VariantOption.objects.filter(name__in=<option_name list>)` (`services.py:57–61`) — **matched by `option_name` only, never scoped by `group_name`**. With live name collisions ("Regular" in Size/Sweetness/Milk; "Hot" in Temperature/Spice Level), a void of one "Regular" selection would resolve to *every* "Regular" option in any group and restore *all* their recipes. Latent (0 variant recipes) but directly couples to AUDIT-001 ISSUE-072 and ISSUE-070.

---

## 6. Live Data Evidence

Queried via `manage.py shell` (read-only):

| Metric | Value |
|---|---|
| `VariantGroup` | 6 |
| `VariantOption` | 19 |
| `CategoryVariantGroup` | 15 |
| `ProductVariantGroup` | **0** |
| `TransactionItemVariant` | 2,483 |
| `RecipeIngredient` variant-scoped (`variant_id NOT NULL`) | **0** |
| `RecipeIngredient` item-scoped | 103 |

**Groups & modifiers (all `is_required=False`, all active):**
- Size (single): Regular +0, **Large +20** · 3 categories
- Temperature (single): Hot +0, **Iced +10**, **Blended +15** · 2 categories
- Sweetness (single): Less Sweet/Regular/Extra Sweet all +0 · 3 categories
- Milk (single): Regular +0, **Oat/Almond/Soy +15** · 3 categories
- Additionals (**multi**): **Extra Shot +20, Whipped Cream +15, Syrup +10** · 3 categories
- Spice Level (single): Mild/Medium/Hot/Extra Hot all +0 · 1 category

**Duplicate option names across groups (ISSUE-072 blast radius):** `Regular` ×3 (Size, Sweetness, Milk), `Hot` ×2 (Temperature, Spice Level). These are exactly the names that would mis-resolve in `_restore_ingredients` once variant recipes exist.

**30-day sales coverage:** 22 completed transactions, 28 line items, **15 line items carried variant selections** (~54%), 24 `TransactionItemVariant` rows, **₱105.00 total `price_modifier`** snapshotted.

**Snapshot drift:** Full scan of all 2,483 `TransactionItemVariant` rows — **0 orphaned** (every `option_name` still matches a live `VariantOption.name`). Drift is currently zero; the risk is structural (no FK + name collisions), not yet realized.

**Price application / collection gap:** Per-line-item check over all 28 recent line items — `final_price − base_price == Σ(TransactionItemVariant.price_modifier)` for **28/28 (0 inconsistent)**. 2 line items had net-positive modifiers; the other 26 selected only ₱0 options. **No price-collection gap: the ~₱105 of modifiers was fully captured into line totals and revenue.** (`base_price` NULL on 0 line items.)

---

## 7. Proposed Tickets

> Numbers to be assigned by Ralph. Severity scale: BLOCKER / High / Medium / Low.

| Proposed name | Type | Severity | Summary | Dependencies / interactions |
|---|---|---|---|---|
| **ISSUE — Required-variant enforcement bypassed when `variant_selections` omitted** | ISSUE | **High** | `services.py:117` gates *all* variant validation behind `if variant_selections:`; an empty/missing key skips required-group enforcement and option-validity checks. Latent only because no live group is required. | Independent. Blocks safe use of `is_required`. Pairs with the new "required groups" capability — fix before any group is marked required. No AUDIT-001 dependency. |
| **FLAG — Variant group resolution implemented twice (serializer vs service)** | FLAG | Medium | `serializers.py:114–135` and `services.py:121–159` independently re-derive the effective-group/required set. They agree now (and `ProductVariantGroup` is unused live) but will drift. | Independent. Should be refactored to one shared resolver before `ProductVariantGroup` overrides are used in production. |
| **FLAG — `_restore_ingredients` matches options by name, not group** | FLAG | **High (latent)** | `services.py:57–61` resolves snapshot options via `name__in` with no `group_name` scope; live name collisions ("Regular" ×3, "Hot" ×2) guarantee wrong-recipe restores on void once variant recipes exist. | **Directly couples AUDIT-001 ISSUE-072 (no-FK snapshot) and ISSUE-070 (dead variant recipes).** Becomes BLOCKER the moment ISSUE-070 is fixed. Strongly argues ISSUE-072 should add a group-scoped or FK-based resolution, not just a raw FK. |
| **FLAG — Variant-scoped recipes remain unauthorable / unused (reconfirm)** | FLAG | Medium | 0/103 recipes are variant-scoped; variant depletion code is exercised zero times. Reconfirms AUDIT-001 ISSUE-070 with quantified evidence and the additional finding that fixing it activates the void-restore-collision bug. | Reconfirms AUDIT-001 **ISSUE-070**. Must be sequenced *after* the `_restore_ingredients` group-scoping fix, else enabling variant recipes introduces silent stock corruption on voids. |
| **FLAG — Variant-vs-variant ingredient overlap silently sums across groups** | FLAG | Low | `_deplete_ingredients` (`services.py:27–35`) accumulates recipes from every selected option with no dedup; two groups specifying the same ingredient stack additively with no documentation or guard. Plausibly intended for add-ons but undefined for the conflicting case. | Depends on ISSUE-070 (no effect until variant recipes exist). Decide intended semantics when authoring variant recipes. |
| **FEATURE — Multi-select cardinality (min/max) for `multi` variant groups** | FEATURE | Low | `multi` groups have no `min_select`/`max_select`; a required `multi` group is satisfied by any single pick. No "choose 2 of 4" capability. | Independent. Builds on the ISSUE above (share the validation site at `services.py:161–192`). |
| **ISSUE — Negative `price_modifier` not shown on receipt** | ISSUE | Low | `receipt_service.py:79` only emits the modifier string when `price_modifier > 0`; a negative (discount) option prints with no price annotation though it is correctly applied to totals. | Independent. |
| **FLAG — Dead `PosTransactionCreateSerializer` trusts client price & drops variants** | FLAG | Low | `serializers.py:263–304` is unreferenced but, if ever wired to an endpoint, would trust client `unit_price` and silently never persist `TransactionItemVariant` (read-only field). Latent footgun. | Independent. Recommend deletion. |

No new `FEATURE` is needed for the price-modifier system itself — **it already exists and works.** The previously-suspected "missing price modifier" FEATURE is **not warranted**.

---

## 8. Recommendation on FEATURE-009 Keystone Status

**FEATURE-009 (IngredientLog ledger) remains the right first keystone; the variant system does not displace it.** The pricing half of the variant system is already functional and revenue-correct (Section 3/6), so there is no urgent variant *capability* gap to elevate. The variant system's real risks (the `_restore_ingredients` name-collision FLAG, the enforcement-bypass ISSUE) are **either latent until variant recipes exist or independent quick fixes** — they do not need to lead.

However, sequencing matters: **AUDIT-001 ISSUE-070 (enable variant recipes) must not ship before** the `_restore_ingredients` group-scoping FLAG and ideally before/with FEATURE-009 — authoring variant recipes today would immediately activate silent stock corruption on voids (wrong-recipe restore via name collision), and FEATURE-009's ledger is exactly the mechanism that would make such corruption auditable and reversible. Recommendation: **FEATURE-009 stays the single keystone; treat the `_restore_ingredients` FLAG and the enforcement-bypass ISSUE as fast-follow blockers gating ISSUE-070, not as a competing keystone.**
