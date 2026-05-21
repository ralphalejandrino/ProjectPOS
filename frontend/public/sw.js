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
const CACHE_NAME = 'tarsierpos-v74'; // canonical cache version
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
  'denied.html',
  'app.js',
  'config.js',
  'components/format.js',
  'dialogs.js',
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
