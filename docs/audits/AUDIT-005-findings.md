# AUDIT-005 Findings — UI/UX Consistency Investigation

**Date:** 2026-05-16
**Auditor:** Claude (read-only audit; no code modified)
**Target:** TarsierPOS frontend — `frontend/public/` (8 static HTML PWA pages + 4 JS + 2 CSS)
**Builds on:** AUDIT-001 (ingredient linkage), AUDIT-002 (variants), AUDIT-003 (X/Z reporting), AUDIT-004 (ops). Cross-refs honored; no duplicate filings.

---

## Executive Summary

TarsierPOS's frontend is **not** Django templates in `canteen/templates/` (that directory does not exist). The entire UI is a **static vanilla-JS PWA** in `frontend/public/`: 8 HTML pages, `app.js`/`config.js`/`dashboard.js`/`payments.js`, `sw.js`, and two stylesheets — `styles.css` (a pre-built, minified Tailwind v3.4.1 purge output) and `shared-styles.css` (a hand-maintained patch file re-adding ~95 Tailwind utilities the purge dropped, plus a few app classes). There is no build step running; CSS is patched by hand. This is the single most important context for every decision below.

The drift is real and concentrated in four areas. **(1) There is a design-token skeleton that is almost entirely dead:** `shared-styles.css` defines `--primary-color/-hover/-light` and `config.js` writes them on every page load, but **zero** HTML/JS/CSS rules consume those variables; the `settings.html` brand-color picker is therefore a no-op; the PWA manifest's brand color is orange `#FF6B00` while every page's nav is a hardcoded blue gradient. **(2) Core interaction primitives are reimplemented per-page:** there are at least five distinct confirm/alert dialog implementations — including two functions both named `showConfirm` with incompatible APIs (Promise vs callback) and a raw-DOM inline-styled confirm built by hand inside `app.js` — plus two unrelated toast implementations and a native `alert()` in `payments.js`. **(3) The "shared" header is dead and the real nav is copy-pasted into 7 files** (`shared-styles.css` `.page-header`/`.menu-item` are unused; one file's nav even carries the comment "NAV (matches dashboard.html)"). **(4) Touch ergonomics on the cashier hot path are below standard:** the cash quick-tender and discount-type buttons on the POS screen are ~28–32px tall against a 44px minimum, while adjacent payment buttons are correctly 58px.

Severity distribution: **0 Tier-1 hard blockers** (the one candidate — a one-frame flash of manager UI to cashiers on `inventory.html`/`ingredients.html` — is Tier 2 because the redirect still fires and `xreport.html` being cashier-visible is by design). **~6 Tier-2** (component divergence, touch targets, role-denied UX, receipt label parity, dead theming/brand mismatch, nav duplication). **~6 Tier-3** (z-index anarchy, color/spacing drift, modal drift, terminology, accessibility foundation).

**Top 3 recommended next moves:**
1. **FEATURE-028 (Design tokens) first** — it is the hard prerequisite for almost every other ticket here. Lock colors, spacing, type, radius, shadow, and a named z-index ladder as CSS custom properties in `shared-styles.css` (the one file already loaded everywhere).
2. **ISSUE-085 (Dialog canonicalization)** — one `confirm`, one `alert`, one `toast`. This is the highest-visibility, highest-frequency inconsistency a user actually sees, and it removes the same-name/different-API footgun.
3. **FEATURE-029 (Shared nav extraction)** — collapse 7 copy-pasted navs into one, which also fixes tagline and POS-emoji drift for free and stops future per-page divergence.

---

## Methodology

**Examined (read in full or substantially):** `shared-styles.css` (all 497 lines), `styles.css` (confirmed as minified Tailwind v3.4.1 purge output), `sw.js`, `manifest.json`, the `<head>`/nav/body-guard regions of all 8 HTML pages, the notification/confirm/modal function bodies in `app.js`, `ingredients.html`, `inventory.html`, `settings.html`, `index.html`, the POS cash/discount controls and on-screen receipt template in `index.html`, role-gate code paths across all pages, and `canteen/receipt_service.py` label region for printed-vs-screen parity. Quantitative sweeps (hex, z-index, terminology, currency, date, a11y attributes) run across all `frontend/public/*.{html,js,css}`.

**Skipped / deferred (with reason):** Full WCAG audit (out of scope per prompt — captured as FEATURE-031 foundation only). Pixel-accurate contrast-ratio computation (spot-flagged, not measured to 3 decimals — belongs in FEATURE-031). Deep `receipt_service.py` field-order audit (FLAG-058 already owns ESC/POS-vs-HTML Z divergence; AUDIT-003 already mapped Z/X content — this audit only adds *label/symbol visual parity*). Live browser/touch measurement via Playwright (static analysis of `py-*`/`height` was sufficient to flag sub-44px targets with citations; live verification belongs in the execution ticket).

**Prior tickets respected (cross-referenced, not refiled):** ISSUE-082 (dashboard month/today mislabel), ISSUE-079 (currency hardcoded), ISSUE-074 (negative price_modifier not on receipt), FLAG-058 (ESC/POS-vs-HTML Z divergence), FLAG-028 (body-visibility role-flash guard), and the deferred "UX: Full UI revamp" page.

---

## Findings by Scope Section

### 1. Asset inventory

| Concern | Finding |
|---|---|
| Templates location | **No `canteen/templates/`.** All UI is `frontend/public/*.html` (8 pages). |
| Sizes | `inventory.html` 1542, `ingredients.html` 1380, `settings.html` 1297, `index.html` 1180, `dashboard.html` 834, `zreport.html` 642, `xreport.html` 295, `login.html` 221. |
| Stylesheets | `styles.css` = minified **Tailwind v3.4.1** purge output (`/*! tailwindcss v3.4.1 */`, single logical line, 28 KB). `shared-styles.css` (497 lines) = hand-written patch re-adding ~95 purged Tailwind utilities + a few app classes. `frontend/public/styles.css` (the empty one) is 0 bytes and unused. |
| Stylesheet linkage | `shared-styles.css` + `styles.css` linked by 7 pages. **`ingredients.html` links neither** the same way (relies on its own inline `<style>` + Tailwind classes) — odd one out. |
| Inline style blocks | `<style>` blocks in `index/ingredients/login/xreport/zreport.html`; `style="…"` attrs: index 10, settings 6, inventory 4, dashboard/zreport/login 1–2. |
| Preprocessor / framework | Tailwind utility classes consumed from a **prebuilt, hand-patched** file. No live Tailwind build. Comment in `shared-styles.css:273-278`: "One-shot fix. Do NOT add individual classes after this." — and classes were added after it anyway (`shared-styles.css:280+`). |

### 2. CSS architecture audit

- **Dead custom properties.** `shared-styles.css:1-5` defines `--primary-color:#1d4ed8`, `--primary-hover:#1e40af`, `--primary-light:#dbeafe`. `config.js:118-119` does `documentElement.style.setProperty('--primary-color'/'--primary-hover', hex)` on load. **`var(--primary*)` is referenced 0 times** in any HTML/JS/CSS. The entire runtime theming pipeline (incl. the `settings.html` color picker, `settings.html:225-229`) writes to a variable nothing reads.
- **Competing "primary blue."** CSS var says `#1d4ed8`; `.menu-item.active`/`.tab-btn.active` use `#2563eb` (`shared-styles.css:61,94-95`); the nav gradient is Tailwind `from-blue-600 to-blue-800` (`#2563eb→#1e40af`). Three different blues for "primary."
- **Color sprawl.** Raw hex in HTML `<style>`/inline: oranges `#FF6B00`, `#FF8C00`, `#FB923C`, `#e55f00`, `#e07800`, `#E3963E` (≥6 orange shades); greens `#16a34a`/`#15803d`/`#22C55E`/`#16a34a`; plus Tailwind's full gray/blue/red/amber/orange/teal/emerald/purple ramps via utilities. The cash button hardcodes `background-color:#16a34a; border:2px solid #15803d` inline (`index.html:153`).
- **Z-index anarchy.** Distinct values in use: `z-50` (×26), `z-[50]` (×6), `z-[60]` (×3), `z-[70]` (×2), `z-[100]`, `z-10` (×2), and raw `z-index: 9998 / 9999 / 10000 / 99999` (the `app.js:513` inline confirm uses `z-index:99999`). `shared-styles.css:373-377` even ships `.z-[9998]…99999` utilities. No semantic ladder.
- **Spacing/radius.** Spacing is mostly Tailwind scale (`py-2/3/4`, `p-5`, `space-y-5/6/8`) — acceptable — but radius is not: `rounded`, `rounded-lg`, `rounded-xl`, `rounded-2xl`, `border-radius:12px` (inline, `app.js:515`), `border-radius:16px` (inline, `login.html:29`) all coexist for similar surfaces.
- **Shadow.** `box-shadow` recipes vary: `shadow`, `shadow-lg`, `shadow-xl`, `shadow-2xl`, plus hand-rolled `0 4px 24px rgba(0,0,0,0.2)` (`app.js:515`) and the shared-styles dark-mode shadow overrides.
- **Dead app CSS.** `.page-header`, `.page-header .container`, `.page-title`, `.menu-dropdown`, `.menu-item`, `.tab-container`, `.back-button`, `.stat-card` are defined in `shared-styles.css:13-136` but the real navs/headers are built with ad-hoc Tailwind on each page (Section 4) — these "consistent" classes are largely unreferenced.

### 3. Component-by-component sweep

**Confirm dialogs — ≥5 distinct implementations for one concept:**
| # | Location | API | DOM | Notes |
|---|---|---|---|---|
| 1 | `app.js` `showCustomConfirm(title,msg,onOk)` | callback | (external) | Used by Clear-Cart path |
| 2 | `app.js:511-539` inline raw-DOM | none (closure) | `createElement` | Hand-built modal, `z-index:99999`, `#dc2626`, `border-radius:12px`, `box-shadow:0 4px 24px` — the *else* branch of the same Clear-Cart handler |
| 3 | `inventory.html:370-386` `showConfirm(title,msg,okLabel)` | **Promise** | `#confirm-modal` (kebab IDs) | |
| 4 | `ingredients.html:1226-1232` `showConfirm(title,msg,onOk)` | **callback** | `#confirmModal` (camel IDs) | **Same name, incompatible contract as #3** |
| 5 | `index.html:497` `confirmAction` | — | — | Separate again |

**Alert / notice:** `settings.html:936-955` `showAlert(message)` builds a dynamic modal (gray gradient header, `z-50`, body-appended); `inventory.html:621-626` `showAlert(title,message,icon='✅')` drives a pre-existing `#alert-modal`. **Same name, different arity.** `payments.js` uses native `alert()` ×3.

**Toast:** `ingredients.html:1327-1334` `showToast(msg,type)` → `#toastContainer`, 3200 ms, `bg-green-600/red-600/gray-800`, `rounded-xl`, no stack cap. `index.html:979-985` `showPrintWarning(msg)` → `fixed bottom-4 right-4`, `bg-yellow-500`, 5000 ms, `rounded-lg`, `z-50`, body-appended. Different position, duration, color system, radius. `dashboard/xreport/zreport/login` have **no** toast primitive at all.

**Buttons:** payment buttons inline-styled with hardcoded hex + `height:58px` (`index.html:153-161`); cash quick-tender `bg-gray-100 py-2 text-sm` (`index.html:187-191`); discount-type `py-1.5 text-xs border-2` (`index.html:206-209`); nav menu button `bg-blue-700 px-4 py-2` (every page). No `.btn-*` class system; no shared disabled/active recipe.

**Modals:** containers vary `rounded-xl shadow-xl` (`inventory.html:1412`) vs `rounded-2xl shadow-2xl` (`settings.html:940`) vs inline `border-radius:12px` (`app.js:515`); backdrop `bg-black bg-opacity-50` (settings) vs `rgba(0,0,0,0.5)` inline (app.js) vs `#modalBackdrop` class (ingredients). No focus trap anywhere; backdrop-click-to-close only in `ingredients.html:1289` and `settings.html:954`; no Escape-to-close observed.

### 4. Layout & navigation consistency

- **Nav is copy-pasted, not shared.** Each of 7 pages hand-codes `<nav class="bg-gradient-to-r from-blue-600 to-blue-800 …">`. `ingredients.html:31` comment: "NAV (matches dashboard.html)". The shared `.page-header`/`.menu-item` classes (Section 2) are unused. Any nav change = edit 7 files.
- **Tagline drift:** `index/dashboard/ingredients.html` → "AI Powered Point of Sale System"; `xreport.html:32` → "Point of Sale System".
- **POS menu emoji drift:** `ingredients.html:58` dropdown "🛒 POS"; `xreport.html:48` dropdown "🏪 POS".
- **`login.html` header** is unrelated: `text-3xl font-bold text-gray-800`, no nav, logo via inline `style="width:64px;height:64px;border-radius:16px"`.
- Container width is consistently `container mx-auto px-4` in nav; content max-widths vary per page (not consolidated).

### 5. Interaction & micro-UX

- **Touch targets below 44px on the cashier hot path:** cash quick-tender buttons `py-2 text-sm` (~32px, `index.html:187-191`) and discount-type buttons `py-1.5 text-xs` (~28px, `index.html:206-209`), while adjacent payment buttons are `height:58px` (`index.html:153-161`). The café is touch-only; these are mis-tap risks during tendering.
- **Role-flash inconsistency:** `dashboard.html:15`, `zreport.html:23`, `settings.html:13` use `<body style="visibility:hidden">` + an inline pre-paint role check (FLAG-028). `inventory.html` and `ingredients.html` do **not** — their role gate runs in `DOMContentLoaded` (`inventory.html:390`, `ingredients.html:576`) *after* paint, so a cashier who navigates to those URLs sees a one-frame flash of manager-only UI before the redirect.
- **`:focus-visible`** is not styled anywhere (zero matches); keyboard focus relies on browser default.
- No consistent `:active` press feedback; `active:scale-95` exists as a utility (`shared-styles.css:216`) but is applied ad-hoc.

### 6. Information architecture per role

- **Permission-denied UX is inconsistent and has no friendly state.** All gating is a silent JS redirect to `index.html`/`login.html`. Method mixes `window.location.replace` (`dashboard.html:23`, `zreport.html:31`, `settings.html:21`) with `window.location.href` (`dashboard.html:223`, `zreport.html:289`, `settings.html:668`, `inventory.html:392`, `ingredients.html:578`). `replace` blocks back-button into a forbidden page; `href` allows back-button into it (then it re-redirects → flicker). Condition phrasing also diverges: `!role || role === 'cashier'` vs `role !== 'manager' && role !== 'admin'` (`zreport.html:31` vs `:288`). No "you don't have access" page anywhere.
- **`xreport.html` is intentionally cashier-accessible** (`xreport.html:149` comment + only `if(!role)` gate) — *not* a leak, but it's the reason it lacks the `visibility:hidden` guard, which reads as inconsistency unless documented.

### 7. Terminology, copy, microcopy

Counts across `*.html` (case-insensitive, indicative): **Item 453 vs Product 185** (both first-class — undecided); Transaction 107 / Void 105 / Sale 24 / Order 19 (four words around one concept); **Discount 175 vs Promo 28**; Cashier 36 (Staff/Operator 0 — consistent, good); PWD 52 / Senior 4. Plus the tagline and POS-emoji drift (Section 4). Button verbs not yet inventoried exhaustively but "Confirm/OK/Yes, Clear/Save/Apply" coexist (`inventory.html:373` default "Confirm", `app.js:523` "Yes, Clear", `settings.html:948` "OK").

### 8. Receipt (printed + on-screen) parity

On-screen mini-receipt (`index.html:417-445`): `Subtotal:`, `TOTAL:`, `₱${x.toFixed(2)}`, discount `-₱${x}`, footer "Thank you for your purchase!". Printed (`canteen/receipt_service.py:91-92`): label is conditionally `Subtotal (VAT-inc):` vs `Subtotal:`, amount formatted `PHP {x:.2f}` (**no ₱ glyph**), `Receipt #:` header, footer from `profile.receipt_header`. Divergences: **currency glyph (`₱` screen vs `PHP ` print)**, **subtotal label** (always "Subtotal:" on screen vs VAT-conditional on print), **footer source** (hardcoded string vs configurable profile). Currency-hardcoding root cause is **ISSUE-079**; Z-field divergence is **FLAG-058**; negative-modifier omission is **ISSUE-074** — this audit only adds the *label/symbol visual-parity* slice (FLAG-064, extends FLAG-058).

### 9. PWA & service worker UX

- **No install prompt** — zero `beforeinstallprompt` handlers.
- **No offline UX** — zero `navigator.onLine` / `'offline'` listeners. `sw.js` fetch handler sends `/api/`+`/canteen/` straight to `fetch()` with **no catch** (offline = silent rejection, no banner); non-API is cache-first with network fallback but **no offline fallback page**.
- **No update prompt** — `sw.js` does `skipWaiting()` + `clients.claim()` only; users are never told a new version activated; open tabs keep stale assets until manual reload, with no `controllerchange`/`updatefound` UX.
- **Manifest/brand mismatch** — `manifest.json` `theme_color`/`background_color` = `#FF6B00` (orange) vs blue UI vs dead `--primary-color` (#1d4ed8). Manifest declares **one** icon `sizes:"any"` `purpose:"any maskable"` (single PNG for both — maskable needs a safe zone); the dedicated `icon-192.png`/`icon-512.png` exist and are SW-cached but **not referenced by the manifest**.

### 10. Accessibility quick pass

- **Zero `aria-label`** across all 8 pages, despite icon-only controls (☰ Menu, ✕ close, qty ±, color swatches).
- **Unlabeled inputs:** `index.html` 2 `<label>` / 13 `<input>` (POS search, cash, qty rely on placeholder); `inventory.html` 24/29.
- **Missing alt:** `inventory.html` has 1 `<img>` without `alt` (product image region, ~`inventory.html:562`).
- **No `:focus-visible`** styling (Section 5); keyboard-only sale completion not verified (belongs in FEATURE-031).
- Heading order not exhaustively verified; scoped into FEATURE-031 as a foundation, non-blocking per prompt.

---

## Cluster Recommendations

| Cluster | Tickets | Rationale & sequencing |
|---|---|---|
| **A — Design System Foundation** | FEATURE-028, FLAG-059, FLAG-060, FLAG-061 | FEATURE-028 (tokens in `shared-styles.css`) is the keystone — it unblocks B, C, D. FLAG-059 (dead theming/brand mismatch) is the most embarrassing finding and rides on the same token work. FLAG-060/061 (z-index, color/spacing) are mechanical refactors *after* tokens exist. |
| **B — Component Canonicalization** | ISSUE-085, FLAG-062 | Highest user-visible payoff. Blocked by FEATURE-028 (needs token names for the canonical components). |
| **C — Layout & Nav** | FEATURE-029 | Collapse 7 navs → 1; absorbs tagline + POS-emoji + role-flash structure. Blocked by FEATURE-028. |
| **D — Cashier Touch UX** | ISSUE-086 | Lock a touch-target token (FEATURE-028) then enforce on POS hot paths. |
| **E — Role-based UI** | ISSUE-087 | Independent; standardize redirect method + add denied state + apply FLAG-028 guard everywhere. |
| **F — Terminology & Microcopy** | FLAG-063 | Independent; dictionary lock, then mechanical sweep (best done alongside C). |
| **G — Receipt/Report Parity** | FLAG-064 | Extends FLAG-058; depends on ISSUE-079 for the currency-symbol half. |
| **H — PWA Polish** | FEATURE-030 | Mostly independent; theme_color fix coordinates with FEATURE-028/FLAG-059. |
| **I — Accessibility Foundation** | FEATURE-031 | Deferred, non-blocking; relates to deferred "UX Full UI revamp." |

---

## Decisions to Lock

(Proposed values — Ralph accepts/amends next session, modeled on AUDIT-001's format.)

1. **Styling source.** Stay with Tailwind utilities via the prebuilt `styles.css` **plus** `shared-styles.css` as the single shared layer. Do **not** introduce a live build now (pilot is active). All new shared design decisions land in `shared-styles.css` `:root`.
2. **Color palette.** Lock: primary `#1d4ed8` (existing var), primary-hover `#1e40af`, primary-light `#dbeafe`, success `#16a34a`, danger `#dc2626`, warning `#f59e0b`, surface `#ffffff`, ink `#1f2937`. Everything else refactors to these via `--color-*`. Retire the 6 ad-hoc oranges unless one is the official brand accent — **decide brand color and make manifest + nav + tokens agree.**
3. **Spacing scale.** 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 (`--space-1…8`), aligned to existing Tailwind usage.
4. **Type scale.** body `1rem`, sm `0.875rem`, xs `0.75rem`, h3 `1.25rem`, h2 `1.5rem`, h1 `1.875rem` (rem, `--text-*`).
5. **Radius scale.** `--radius-sm` 0.5rem (inputs/buttons), `--radius-md` 0.75rem (cards), `--radius-lg` 1rem (modals). Kill inline `12px`/`16px`.
6. **Shadow recipes.** 3 only: `--shadow-subtle`, `--shadow-raised`, `--shadow-modal`.
7. **Z-index ladder.** `--z-base:0`, `--z-dropdown:1000`, `--z-sticky:1010`, `--z-modal-backdrop:1040`, `--z-modal:1050`, `--z-toast:1060`. Ban raw `9999`/`99999`.
8. **Component canonicalization order.** Buttons → dialogs (confirm/alert/toast) → modals → nav. Buttons first (highest visibility, lowest scope).
9. **Confirmation pattern.** One custom modal-based `confirm(title, message) → Promise<boolean>`. No native `confirm()`/`alert()`. Retire the four duplicates and the `app.js` inline raw-DOM fallback.
10. **Toast.** Position bottom-right, 3500 ms, max 3 stacked, severity colors from tokens, `--radius-md`.
11. **Terminology dictionary.** **Product** (catalog noun) / **Item** (line on a transaction) — define the split rather than pick one, since both are legitimately needed; **Sale**=completed customer transaction, **Void**=POS reversal, **Delete**=admin record removal (keep distinct); **Discount** (retire "Promo"); **PWD**/**Senior Citizen** spelled per BIR. Tagline: "AI Powered Point of Sale System" everywhere.
12. **Currency / number / date.** Drive currency from `BusinessProfile.currency` (ISSUE-079); display `₱1,234.50` (grouping + 2dp) via one shared formatter; dates `MMM D, YYYY` for humans, ISO for data attrs. Replace ad-hoc `toFixed(2)`/`toLocaleString` mix with one util.
13. **Touch target minimum.** 44×44 enforced globally; 48×48 on POS index hot paths (`--touch-min`, `--touch-min-pos`).
14. **Refactor strategy.** Incremental: "new code uses tokens; old code refactored as touched," except FEATURE-028 + ISSUE-085 + FEATURE-029 which are done as focused PRs because they are the foundation everything else inherits.

---

## Ticket Roll-Up

| Ticket | Title | Tier | Cluster | Depends on | Blocks / Prereq for |
|---|---|---|---|---|---|
| FEATURE-028 | Design-token foundation (color/space/type/radius/shadow/z-index CSS vars) | 3 | A | — | FLAG-060, FLAG-061, ISSUE-085, FEATURE-029, ISSUE-086 |
| FLAG-059 | Dead theming pipeline + brand-color mismatch (var unused, picker no-op, manifest orange vs blue UI) | 2 | A | — | relates FEATURE-028, FEATURE-030 |
| FLAG-060 | Z-index anarchy (z-50…99999 magic numbers) | 3 | A | FEATURE-028 | — |
| FLAG-061 | Color/spacing/radius drift (6 oranges, 3 blues, inline hex) | 3 | A | FEATURE-028 | — |
| ISSUE-085 | ≥5 divergent confirm/alert/toast impls incl. same-name/different-API `showConfirm` | 2 | B | FEATURE-028 | — |
| FLAG-062 | Modal pattern drift (radius/backdrop/no focus trap/escape) | 3 | B | FEATURE-028 | relates ISSUE-085 |
| FEATURE-029 | Extract single shared nav/header; retire dead `.page-header`; fix tagline + POS emoji | 2 | C | FEATURE-028 | — |
| ISSUE-086 | Sub-44px touch targets on POS cashier hot path | 2 | D | FEATURE-028 | — |
| ISSUE-087 | Inconsistent permission-denied UX + role-flash on inventory/ingredients | 2 | E | — | relates FLAG-028 |
| FLAG-063 | Terminology & microcopy drift (Item/Product, Sale/Order/Transaction, Discount/Promo) | 3 | F | — | pairs with FEATURE-029 |
| FLAG-064 | On-screen vs printed receipt label/symbol parity | 2 | G | ISSUE-079 | extends FLAG-058 |
| FEATURE-030 | PWA UX gaps (no install/offline/update prompt; manifest icons/theme) | 2 | H | — | relates FLAG-059 |
| FEATURE-031 | Accessibility foundation (aria-label, labels, alt, focus-visible) | 3 | I | — | relates deferred "UX Full UI revamp" |

**14 tickets** — 0 Tier-1, 6 Tier-2, 7 Tier-3 (FEATURE-028 counted Tier-3 as pure design-debt foundation). Cross-refs: ISSUE-079, ISSUE-074, ISSUE-082, FLAG-058, FLAG-028, deferred "UX Full UI revamp" — all amended/cross-referenced, none refiled.
