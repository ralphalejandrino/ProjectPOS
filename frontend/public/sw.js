const CACHE_NAME = 'tarsierpos-v46'; // canonical cache version (v46: purge any stale API responses cached by older SW)
const ASSETS = [
  'index.html',
  'login.html',
  'dashboard.html',
  'inventory.html',
  'ingredients.html',
  'settings.html',
  'xreport.html',
  'zreport.html',
  'app.js',
  'config.js',
  'dashboard.js',
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
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, response.clone()));
        }
        return response;
      });
    })
  );
});
