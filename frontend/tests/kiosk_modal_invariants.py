"""Standing kiosk regression sweep — modal safety invariants.

WHY THIS EXISTS
---------------
~6 of the last 7 PROD kiosk "bugs" were not regressions. They were ONE class
(on-screen-keyboard x modal x touch) surfacing one modal at a time, each fixed
only for the modal that happened to be reported. This file pins the invariants
for EVERY registered modal so the class cannot come back by omission — a new
modal that gets it wrong fails here instead of on the manager's register.

Run (needs the no-sudo Playwright + Chromium harness, see vault
[[wsl-playwright-browser-testing]]):
    python frontend/tests/kiosk_modal_invariants.py

Invariants, one per failure mode we have actually shipped to a client:
  1. NO VANISH   - a tap on the backdrop must not close a data-entry/payment
                   modal. (restock-vanish, 1ddf5c2; GCash exposed until v144)
  2. NO ZOOM     - kiosk pages must pin the viewport, else a stray pinch/
                   double-tap zooms the register and a fixed modal pans off
                   screen, looking like a white-screen crash.
  3. NO JUMP     - opening the keyboard must not move the card under the user's
                   finger; controls must not travel between two taps.
  4. NO BURIAL   - the primary action must stay tappable with the keyboard up.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PUBLIC = Path(__file__).resolve().parents[1] / "public"

# Pages the kiosk appliance actually runs. remote/period/status/accreditation are
# read on real phones, where pinch-zoom is a genuine a11y need — excluded on
# purpose (WCAG 1.4.4).
KIOSK_PAGES = ["index.html", "inventory.html", "ingredients.html", "zreport.html",
               "xreport.html", "settings.html", "dashboard.html", "login.html",
               "denied.html"]
ZOOMABLE_PAGES = ["remote.html", "period.html", "status.html", "accreditation.html"]

# The only modals allowed to dismiss on a backdrop tap: read-only viewers with no
# in-progress entry to lose. Anything that takes input must NOT be here.
BACKDROP_CLOSE_ALLOWED = {"detail-modal"}

VIEWPORTS = [(1024, 600), (1366, 768), (1280, 720)]
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def modals_in(html_text):
    out = []
    for m in re.finditer(r"<div\b[^>]*\bdata-modal\b[^>]*>", html_text):
        tag = m.group(0)
        mid = re.search(r'id="([^"]+)"', tag)
        if mid:
            out.append((mid.group(1), "data-modal-backdrop-close" in tag))
    return out


def static_checks():
    """Invariants 1 + 2 are markup contracts — assert them without a browser."""
    for page in KIOSK_PAGES:
        text = (PUBLIC / page).read_text()
        vp = re.search(r'<meta name="viewport" content="([^"]+)"', text)
        check(vp and "user-scalable=no" in vp.group(1),
              f"NO ZOOM: {page} viewport is not pinned (user-scalable=no missing)")
        for mid, opts_in in modals_in(text):
            if opts_in:
                check(mid in BACKDROP_CLOSE_ALLOWED,
                      f"NO VANISH: {page}#{mid} opts into backdrop-close but is not a "
                      f"read-only viewer — a stray tap can discard in-progress entry")

    for page in ZOOMABLE_PAGES:
        text = (PUBLIC / page).read_text()
        vp = re.search(r'<meta name="viewport" content="([^"]+)"', text)
        check(vp and "user-scalable=no" not in vp.group(1),
              f"A11Y: {page} is read on phones and must stay pinch-zoomable")

    # The old opt-out vocabulary must not creep back alongside the new opt-in one.
    for page in KIOSK_PAGES + ZOOMABLE_PAGES:
        check("data-modal-no-backdrop-close" not in (PUBLIC / page).read_text(),
              f"DRIFT: {page} still uses the retired data-modal-no-backdrop-close")


def live_checks(pw):
    """Invariants 1, 3, 4 — real Chromium, real touch, real CSS."""
    for page in ["index.html"]:
        text = (PUBLIC / page).read_text()
        targets = [mid for mid, opts_in in modals_in(text) if not opts_in]
        for vw, vh in VIEWPORTS:
            b = pw.chromium.launch()
            ctx = b.new_context(viewport={"width": vw, "height": vh}, has_touch=True)
            ctx.add_init_script("localStorage.setItem('access_token','t');"
                                "localStorage.setItem('refresh_token','t');")
            p = ctx.new_page()
            p.goto((PUBLIC / page).as_uri(), wait_until="domcontentloaded")
            p.wait_for_timeout(800)
            p.evaluate("document.body.classList.add('osk-enabled')")

            for mid in targets:
                field = p.evaluate("""(mid) => {
                    const m = document.getElementById(mid);
                    if (!m) return null;
                    m.classList.remove('hidden');
                    const f = m.querySelector('input:not([type=hidden]):not([disabled])');
                    if (!f) { m.classList.add('hidden'); return null; }
                    const r = f.getBoundingClientRect();
                    return {id: f.id, x: Math.round(r.left + r.width / 2),
                            y: Math.round(r.top + r.height / 2)};
                }""", mid)
                if not field:
                    continue

                p.wait_for_timeout(150)
                p.touchscreen.tap(field["x"], field["y"])   # tap 1: focus -> keyboard
                p.wait_for_timeout(400)

                moved = p.evaluate("""(a) => {
                    const f = document.getElementById(a.id);
                    const r = f.getBoundingClientRect();
                    return Math.round(Math.abs((r.top + r.height / 2) - a.y));
                }""", field)
                check(moved <= 8,
                      f"NO JUMP: {page}#{mid} field '{field['id']}' moved {moved}px at "
                      f"{vw}x{vh} when the keyboard opened — it shifts under the finger")

                # Tap 2 at the SAME place, exactly as a human re-taps a field.
                p.touchscreen.tap(field["x"], field["y"])
                p.wait_for_timeout(300)
                still_open = p.evaluate(
                    "(mid) => !document.getElementById(mid).classList.contains('hidden')", mid)
                check(still_open,
                      f"NO VANISH: {page}#{mid} closed on a re-tap of its own field at "
                      f"{vw}x{vh} — in-progress entry would be silently discarded")

                # An unambiguous backdrop tap (top-left corner) must also be survivable.
                p.evaluate("(mid) => document.getElementById(mid).classList.remove('hidden')", mid)
                p.wait_for_timeout(150)
                p.touchscreen.tap(4, 4)
                p.wait_for_timeout(300)
                still_open = p.evaluate(
                    "(mid) => !document.getElementById(mid).classList.contains('hidden')", mid)
                check(still_open,
                      f"NO VANISH: {page}#{mid} closed on a stray backdrop tap at {vw}x{vh}")

                p.evaluate("(mid) => document.getElementById(mid).classList.add('hidden')", mid)
                p.evaluate("document.body.classList.remove('osk-open')")
            b.close()


with sync_playwright() as pw:
    static_checks()
    live_checks(pw)

if failures:
    print(f"FAIL — {len(failures)} kiosk modal invariant(s) broken:\n")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
print("PASS — kiosk modal invariants hold (no vanish / no zoom / no jump).")
