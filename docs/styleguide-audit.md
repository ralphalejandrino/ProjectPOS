# Styleguide Coverage Audit — ISSUE-092 Phase 1
*Generated 2026-05-18 against `docs/styleguide.html` rev pre-rebuild and `frontend/public/*` HEAD.*

The styleguide is the canonical visual contract for B4–B7 and every future UI change
(see header of `frontend/public/shared-styles.css`). Anything the app renders that
isn't pictured here is, by definition, drifting.

This audit walks the styleguide section-by-section, then walks the codebase
component-by-component, and lists what's present, what's missing, and what's
stale.

---

## A · Currently in styleguide

| § | Section | Components rendered |
|---|---------|---------------------|
| 1.1–1.9 | Tokens | primary / semantic / surface colors, spacing 1–8, type xs–2xl, radius sm/md/lg, shadow subtle/raised/modal, z-ladder, touch targets 44 vs 48 |
| 2 | Buttons | primary / secondary / danger / ghost · sm / md / lg · default / hover / disabled / loading · with icon · POS hot-path |
| 3 | Inputs | text / number / email / password / search · default / focus / error / success / disabled |
| 4 | Choices | select, checkbox (checked/unchecked/disabled), radio (SC / PWD / Promo) |
| 5 | Dialogs & toasts | confirm normal, confirm danger, alert, toasts (info/success/warning/danger) |
| 6 | Modals | one cash-payment modal example |
| 7 | Cards | flat / raised / hoverable |
| 8 | Tables | populated / empty / loading |
| 9 | Nav | one nav-bar-gradient with user info + menu button (no role variants) |
| 10 | States | empty / error / permission-denied |
| 11 | Receipt | one ASCII receipt preview |
| 12 | Typography | h1 / h2 / h3 / h4 / body / muted body / inline code / link |

---

## B · Missing — not pictured in styleguide

Cross-referenced against `frontend/public/*.html` and `payments.js` / `dialogs.js`.

| Component | Where it lives in code | Severity |
|-----------|------------------------|----------|
| **GCash payment modal** (QR display, phone input, reference input, confirm/cancel) | `index.html:906–973` | high — entire POS hot-path variant |
| **Maya payment modal** (QR display, phone input, reference input, confirm/cancel) | `index.html:976–1043` | high |
| **Card payment** disabled placeholder button ("VISA · MC — Coming Soon") | `index.html:131` | medium — pictured as POS button but no card-modal section |
| **Discount selector** — SC / PWD / Promo three-button row before any type is chosen | `index.html:174–213` (cash modal); identical block in gcash + maya modals | high |
| **Discount applied state** — selected button, OSCA/PWD ID input visible, % input visible (promo), red `-₱X.XX` display row, "Remove discount" link | `index.html:193–212` | high |
| **Apply Discount toggle** — dashed-border ghost button that reveals the selector | `index.html:215–220` | medium |
| **Logo upload — empty state** (no preview, "Upload Logo" button + helper text) | `settings.html:76–89` | medium |
| **Logo upload — preview state** (16×16 preview tile + Upload button) | `settings.html:79` `logo-preview` not `.hidden` | medium |
| **Logo upload — error state** | `settings.html:1157` `alertDialog('Logo upload failed')` (toast/alert path) | low |
| **QR upload control** (file input + "Upload QR" button, used twice for GCash + Maya in settings) | `settings.html:290–315` | medium |
| **Nav — role variants** (admin sees all 7 links; manager sees 6; cashier sees POS only) | `components/nav.js:8–16` `LINKS[].roles` | medium — informational; not pictured |
| **Nav — dropdown menu open state** | `components/nav.js:84–112` (the `#dropdown-menu` block) | medium |
| **Live clock + user info chip** in nav | `components/nav.js:95–99` | low — partly covered in §9 but not labeled |
| **Variant picker modal** (size / add-on chooser before adding to cart) | `index.html:266–280` | low — POS-only |
| **Open shift prompt modal** | `index.html:239–252` | low |
| **Shift-opened success banner** (fixed top center, green) | `index.html:234–236` | medium — distinct from toast pattern |
| **Cash quick-add denomination buttons** (₱20/50/100/500/1000 grid) | `index.html:157–163` | low |
| **Cash insufficient warning panel** (red box inside cash modal) | `index.html:165–167` | medium |
| **Cash change-due panel** (green box inside cash modal) | `index.html:169–172` | medium |
| **Stat card** (border-left accent, `.stat-card` in `shared-styles.css:99–115`) | `shared-styles.css:99` + `dashboard.html` | medium — dashboard-wide pattern |
| **Status badges** (In stock / Low / Out) — present in §8 as inline text, but not as the `bg-*-100 + text-*-800` pill pattern used in inventory tables | `shared-styles.css:148–152` `.dark .bg-*-100` rules | low |
| **denied.html — "You tried to access:" path box** | `denied.html:125–128` | low — §10 shows the card but not the from-path detail |
| **Loading spinner** (animated, used inside payment-modal processing state) | `index.html:259` `animate-spin` | low |
| **Dark mode** rendering of any component | `shared-styles.css:117–159` | medium — entirely missing |
| **Receipt link out** — receipt section currently embeds a small ASCII preview; receipt-design.html (5 variants) is the canonical source | `docs/receipt-design.html` (322 lines) | medium — should link, not duplicate |

---

## C · Out of date — pictured but doesn't match shipped code

| Component | Drift |
|-----------|-------|
| **Token block** | Duplicated inline in `styleguide.html:12–55` rather than loaded from `shared-styles.css`. Already drift-prone — if a token changes in shared-styles, the styleguide silently lies until a human re-copies. |
| **Receipt §11** | Shows a single hardcoded ASCII preview. The real receipt has 5 design variants and a live decision page in `docs/receipt-design.html`. The styleguide preview is fine for shape, but should explicitly link out. |
| **Toast §5.4** | Markup-only static example. The shipped `dialogs.js:219–242` uses `#toast-stack` positioning (bottom-right), 3500ms duration, 3-stack cap, and a `.toast-leaving` transition class — none of which are pictured or explained as a contract. |
| **Dialog §5.1–5.3** | Pictured without backdrop / focus-trap / esc-close framing. The shipped `dialogs.js:46–149` enforces all three. Worth a contract note. |
| **Nav §9** | Single static example. Doesn't reflect:<br>· role-gated link visibility (`nav.js:33`)<br>· dropdown menu open state<br>· live clock (`nav.js:50–61`)<br>· cached business name / tagline override (`nav.js:38–48`) |
| **Permission-denied §10** | Renders a generic dashed-border block with a generic copy. `denied.html` ships a real card with shadow, "You tried to access:" path display, and two-button stack (Back / Sign out). |
| **Payment button row §2 "Payment hot-path"** | Shows Cash / GCash / Maya as three styled `.btn-lg` buttons. Real POS uses a `<img>` logo inside the GCash and Maya buttons (`index.html:125–130`) and a disabled "VISA · MC — Coming Soon" stub for card. |

---

## D · Coverage map for the rebuild

The rebuilt styleguide must, at minimum, add:

1. `<link rel="stylesheet" href="../frontend/public/shared-styles.css">` — tokens loaded from a single source.
2. Payment modal variants — Cash (existing), GCash, Maya, Card-placeholder.
3. Discount selector — collapsed toggle / type chooser / SC-or-PWD with ID input applied / Promo with % applied / remove-discount link.
4. Logo upload — empty / preview / error.
5. QR upload row — used by Settings for GCash and Maya.
6. Nav role variants — admin (7 links) / manager (6 links) / cashier (1 link). Informational table, no behavior wiring.
7. Nav dropdown — open menu state.
8. Denied state — match `denied.html` card shape + from-path box.
9. Touch-target-pos vs touch-target — kept (already at §1.9), with explicit "where to use which" copy.
10. Stat card — `.stat-card` with border-left accent.
11. Status badge pill pattern (the `bg-*-100 text-*-800` pattern).
12. Cash modal sub-components — denomination quick-adds, insufficient warning, change-due panel.
13. Shift-opened success banner — fixed-position banner pattern (distinct from toast).
14. Variant picker modal — title / option group / cancel + add buttons.
15. Loading spinner (the `animate-spin` ring) — pictured + sized variants.
16. Dialog/toast contracts — caption note under each: backdrop, focus trap, esc-close, stack cap, duration.
17. Receipt section — explicit link to `docs/receipt-design.html`, short shape preview only.
18. Sticky left-side nav listing every section.
19. Each section: rendered example + a code snippet beneath it.

Out of scope (deferred):
- Dark mode rendering of every component (track as a follow-up issue; the dark-mode CSS exists but pictures every component twice).
- Print-mode rendering.
- Mobile responsive shrink-down behavior of cards / tables.

---

*End of audit. Phase 2 implements every item in §D.*
