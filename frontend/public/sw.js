// v62: ISSUE-105 — unofficial Z mode (pre-BIR-accreditation support)
// v63: ISSUE-106 + 107 + 104 — persistent shift indicator,
// open-shift modal, sale-without-shift enforcement
// v64: ISSUE-108 — cashier role exception for zreport.html?close=1
// v65: FEATURE-036 — shift status in header, Close Shift moved to dropdown
// v66: ISSUE-110 — fix shift indicator render (always-visible status pill)
// + symmetric Open/Close shift dropdown entries
// v67: FEATURE-037 — tappable shift indicator + cross-account open-shifts panel
// v68: ISSUE-099 — printer settings overhaul (transport mode + paper/font)
// v69: FEATURE-020 — local-status MVP page
// v70: FEATURE-006 — credential (password) reset in User Management
// v71: FEATURE-039 — network (WiFi) management with auto-revert
// v72: FEATURE-006 — reset-password modal styleguide compliance fix
// v73: FEATURE-040 — receipt overhaul + on-screen preview
// v74: ISSUE-067 + FEATURE-039 — match Create Account & Wi-Fi inputs to BIR styling
// v75: ISSUE-113 + FEATURE-041 — fix quick-login grid perms; account rename + hard delete
// v76: FEATURE-041 — consolidate per-user actions into a single Manage Account modal
// v77: FEATURE-041 — nested-dialog z-index (--z-dialog) + Danger Zone button hierarchy
// v78: FEATURE-041 — replace dead (unpurged) Tailwind classes in Manage modal; fix spacing
// v79: DEPLOY pre-flight — re-add purge-dropped utilities for FEATURE-039/040 (banner/preview)
// v80: FLAG-072 (loopback-relaxed quick-login throttle) + FEATURE-043 (on-screen keyboard)
// v81: FEATURE-044 — per-user page-access toggles (nav + guards + Manage modal)
// v84: FEATURE-031 — accessibility foundation (aria-labels, input labels,
// img alt, :focus-visible). NOTE: const was already at v83 (ahead of the
// changelog above); bumped to the real next version, not the plan's stale v69.
// v85: FEATURE-030 — PWA UX (install prompt, offline banner, SW update notice
// in config.js). Bumped to re-precache the updated config.js. The existing
// skipWaiting + clients.claim below already drives the controllerchange-based
// update prompt, so no SW logic change was needed.
// v87: B13 — period report, owner insights, and admin remote ops view
// (FEATURE-013 / FEATURE-014 / FEATURE-026, FLAG-055 partial). New pages +
// nav/config page-access keys; bumped to precache the three pages.
// v88: FEATURE-010 — multi-select cardinality (min/max) hints + client-side
// enforcement in the variant picker.
// v89: FEATURE-015 — refund accounting (manager/admin refund button, X/Z
// refund section).
// v90: FEATURE-016 — split / multi-payment transactions (PaymentLine model,
// split UI in the cash modal, PaymentLine-based X/Z payment breakdown).
// v91: FEATURE-035 — post-accreditation Z-counter reset (accreditation.html
// admin page + admin nav link).
// v93: ISSUE-113 — unit labels on recipe builder / ingredient / restock
// inputs (ingredients.html).
// v94: B-UI-CLEANUP — ISSUE-115 (color presets fixed, token palette),
// ISSUE-116 (insights widgets onto dashboard, insights.html removed),
// ISSUE-117 (Remote View out of dropdown), ISSUE-118 (period report →
// Weekly Performance Report). One bump for the whole batch.
// v95: ISSUE-119 — repair remaining corrupted <butto tags (inventory
// row/modal buttons, sidebar toggle).
// v96: B-FIX-DASH — ISSUE-120 (dashboard insights widgets populate on every
// page load: init moved ahead of the awaited chain + pageshow repopulation
// on bfcache restores). One bump for the batch.
// v97: B-REPORT — ISSUE-121 (Weekly Performance Report rework: defaults to
// last completed Mon–Sun week, headline cards with per-metric WoW, daily
// revenue bars + busiest day/hour, payment mix chart+table, worst sellers,
// weekly cashier summary, inventory notices; inventory.html ?q= deep-link).
// v98: FEATURE-046 — ingredient-derived "makeable" stock (read-time, zero
// migration): inventory "On hand (counted)" vs "Can make now" columns and the
// dashboard low-stock widget showing what recipe items can make now.
// v104: BUG-005 — proactive + single-flight token refresh in config.js so an
// idle kiosk's 15-min access token never lapses and the wake-up call burst
// can't race the rotation/blacklist into a logout.
// v105: FLAG-079 — on-screen keyboard caps-lock latch (three-state shift:
// off/shift/caps) so all-caps no longer needs re-tapping ⇧ per letter.
// v106: ISSUE-121-FU-F/G — weekly report restock detail table + net cash flow
// headline (period.html); consistent X/Z/Weekly thermal layout (FU-H).
// v109: BUG-008/009/010 — archived-items view + restore (inventory.html),
// reliable frontend-triggered receipt auto-print (index.html), and
// numpad value/focus retention (inventory.html + keyboard.js).
const CACHE_NAME = 'tarsierpos-v111'; // canonical cache version
const ASSETS = [
  'index.html',
  'login.html',
  'dashboard.html',
  'inventory.html',
  'ingredients.html',
  'settings.html',
  'xreport.html',
  'zreport.html',
  'status.html',
  'period.html',
  'remote.html',
  'accreditation.html',
  'denied.html',
  'app.js',
  'config.js',
  'components/format.js',
  'dialogs.js',
  'keyboard.js',
  'payments.js',
  'styles.css',
  'shared-styles.css',
  'manifest.json',
  'icon-192.png',
  'icon-512.png',
  'assets/tarsier-icon.png',
  'assets/gcash-logo.png',
  'assets/maya-logo.png',
  'icons/tarsier-logo.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.url.includes('/api/') || event.request.url.includes('/canteen/')) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((response) => {
        // Cache successful same-origin responses (images, JS, CSS, HTML)
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseToCache));
        }
        return response;
      });
    })
  );
});
