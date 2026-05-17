# Brand Decision — TarsierPOS

**Date:** 2026-05-17
**Decision:** **BLUE** (`#1d4ed8`) — chrome and CTAs share the locked primary.
**Closes:** FEATURE-032 brand-color decision.
**Inputs reviewed (by looking, not by defaulting):**
- `docs/brand-blue.html` — chrome + CTA `#1d4ed8`
- `docs/brand-orange.html` — chrome + CTA `#ff6b00`
- `docs/brand-dual.html` — blue chrome + orange `#ff6b00` CTA accent

## Locked palette

| Token | Value | Role |
|---|---|---|
| `--color-primary` | `#1d4ed8` | nav, CTAs, active state, focus ring |
| `--color-primary-hover` | `#1e40af` | press/hover state |
| `--color-primary-light` | `#dbeafe` | selected backgrounds, badges |

Cash payment stays semantic green (`--color-success #16a34a`). Danger semantic stays red (`--color-danger #dc2626`).

## Rationale

1. **Coherence with the locked palette.** AUDIT-005 Decision 2 already locked `--primary-color: #1d4ed8`. Blue keeps that promise instead of relitigating.
2. **Fixes the manifest mismatch in the right direction.** Today's nav is blue and the manifest is orange; the audit called that "the most embarrassing finding." The cheaper, less disruptive fix is to bring the manifest to the UI (one JSON edit) rather than recolor every page.
3. **8-hour-shift ergonomics.** Cashiers stare at this UI all shift. Blue chrome reads as calm, recedes, lets the cart and Cash button do the talking. Orange chrome (mockup #2) is loud and clashes with the green Cash button — too many high-attention colors on one screen.
4. **Dual was tempting, but added a second primary.** The dual mockup (orange accent + blue chrome) is visually nice, but introduces a second brand color that needs its own scale, dark-mode mapping, and decision rules for every component. That's scope this batch does not need and the token system this batch is locking does not have.
5. **Brand-identity preserved by the icon, not the tokens.** The orange tarsier mascot remains the brand asset — `assets/tarsier-icon.png`, the favicon, app icon. Mascot colors and system tokens are separate concerns; keeping them separate is normal.

## Consequences

- `manifest.json` `theme_color` and `background_color` change to align with the blue chrome (Phase 3).
- `:root` tokens in `shared-styles.css` adopt the locked palette (Phase 2).
- `.menu-item.active` / `.tab-btn.active` hardcoded `#2563eb` retire in favor of `var(--color-primary)`.
- The `settings.html` brand-color picker becomes a real runtime override once `var(--color-primary)` is consumed by actual rules (this batch wires that up).
- Orange utilities in `shared-styles.css` (`.bg-orange-*`, `.text-orange-*`) stay for badge/status use (`OUT_OF_STOCK`, low-stock) but no longer carry brand meaning.

## Reversal cost

Token-driven. Flipping later means editing `:root` + `manifest.json` + the four reference docs. No per-page rework, no copy-paste sweep. That's the whole point of locking via tokens now.
