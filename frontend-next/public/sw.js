// VibeScape service worker.
// Goal: installability (PWA) + fast repeat loads of static assets.
// Strategy:
//   - Static app shell: cache-first, network fallback, revalidate in background.
//   - API + Spotify SDK + third-party audio/video: network-only (no caching).
//   - Never cache Spotify OAuth callbacks or any query-stringed navigation.

const VERSION = 'v2';
const STATIC_CACHE = `vibescape-static-${VERSION}`;

// React emits content-hashed bundles (/assets/index-<hash>.js), so they
// cannot be precached by name. Only the shell is listed; hashed assets are
// cached on first fetch and are immutable, so they never need revalidating.
const APP_SHELL = [
  '/',
  '/index.html',
  '/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(APP_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== STATIC_CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

function isStaticAsset(url) {
  return (
    url.origin === self.location.origin &&
    !url.pathname.startsWith('/api/') &&
    !url.pathname.startsWith('/auth/') &&
    !url.pathname.startsWith('/media/') &&
    !url.pathname.startsWith('/preview/')
  );
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  if (!isStaticAsset(url)) return;

  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((resp) => {
          if (resp && resp.status === 200 && resp.type === 'basic') {
            const copy = resp.clone();
            caches.open(STATIC_CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
