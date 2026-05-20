// v62: ISSUE-105 — unofficial Z mode (pre-BIR-accreditation support)
// v63: ISSUE-106 + 107 + 104 — persistent shift indicator,
// open-shift modal, sale-without-shift enforcement
// v64: ISSUE-108 — cashier role exception for zreport.html?close=1
// v65: FEATURE-036 — shift status in header, Close Shift moved to dropdown
// v66: ISSUE-110 — fix shift indicator render (always-visible status pill)
// + symmetric Open/Close shift dropdown entries
const CACHE_NAME = 'tarsierpos-v66'; // canonical cache version
const ASSETS = [
  'index.html',
  'login.html',
  'dashboard.html',
  'inventory.html',
  'ingredients.html',
  'settings.html',
  'xreport.html',
  'zreport.html',
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
