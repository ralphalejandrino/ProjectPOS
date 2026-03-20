const CACHE_NAME = 'tarsierpos-v22';
const ASSETS = [
  'index.html',
  'login.html',
  'dashboard.html',
  'inventory.html',
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
  // Simple cache-first strategy for static assets, 
  // but we might want network-first for index.html if it's dynamic
  event.respondWith(
    caches.match(event.request)
      .then((response) => {
        return response || fetch(event.request);
      })
  );
});
