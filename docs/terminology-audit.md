# Terminology Audit — FLAG-063

Locked dictionary sweep performed alongside FEATURE-029 (shared nav).
Reference: AUDIT-005-findings.md §7, roadmap locked decision #20.

## Changed (user-facing strings only)

| Was | Now | Where |
| --- | --- | --- |
| "🏷️ Promo" (button label, 3 places) | "🏷️ Discount" | `frontend/public/index.html` — cash, gcash, maya discount picker |
| "Promo Discount (%)" (cart line-item label) | "Discount (%)" | `frontend/public/app.js` |
| "Promo Discount" (transaction-detail row label) | "Discount" | `frontend/public/dashboard.html` |
| "Promo Discount" (settings toggle heading + HTML comment) | "Discount" | `frontend/public/settings.html` |
| "Point of Sale System" (drifted tagline) | "AI Powered Point of Sale System" | `frontend/public/xreport.html`, `frontend/public/zreport.html` — now sourced from `components/nav.js` |
| Mixed `🏪 POS` / `🛒 POS` dropdown emoji | `🛒 POS` (single source in `components/nav.js`) | all 7 pages |

## Deliberately left alone

| Term | Reason |
| --- | --- |
| `promo` (object key, query param, CSS id like `cash-btn-promo`, `discount_type='promo'`) | Code identifier, not user-facing. Renaming would require coordinated API change. |
| `promo_discount_enabled`, `promo_discount_rate` (biz_profile fields) | API field names — owned by backend, out of scope for this sweep. |
| `🏪` on the settings "Business Information" card heading | Not a POS-dropdown emoji; the rule (locked POS emoji = `🛒`) applies only to the nav dropdown entry, not unrelated decorative emoji. |
| `Item` vs `Product` | Both legitimate per dictionary — `Item` for catalog rows, `Product` in the POS grid context. |
| `Sale` vs `Transaction` vs `Order` | All three are legitimate distinct concepts (Sale = revenue event, Transaction = DB row, Order = cart-state). |
| Confirm-button verbs ("Confirm Void", "Confirm GCash", "Save", "Cancel", "Close") | Already aligned with B4 confirm dialog conventions. |

## How the canonical tagline survives runtime override

`components/nav.js` writes `AI Powered Point of Sale System` into `#site-tagline`
when it renders. `config.js applyCachedBranding()` and `loadSiteName()` may
override it with a per-business tagline from the business profile — that is
intentional and locked. The drift this sweep removed was *static HTML* shipping
the wrong default before any cached profile loaded.
