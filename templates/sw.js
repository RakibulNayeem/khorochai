{% load static %}/* KhorochAi service worker. Served at /sw.js so it controls the whole site.
 *
 * What it caches: static files (css, js, icons) and the few CDN files the pages use. Nothing else.
 * Pages and every other response are per-user, so they always go to the network and are never stored here:
 * one person's data can't be served to another. When a page can't load, /offline/ (public, same for all) is shown.
 * Build: {{ build_id }} (a new deploy changes this file, which is how the browser notices an update). */
const VERSION = '{{ build_id }}';
const CACHE = 'khoroch-static-' + VERSION;
const OFFLINE_URL = '{% url "offline" %}';
const PRECACHE = [
  OFFLINE_URL,
  '{% static "css/app.css" %}',
  '{% static "js/offline.js" %}',
  '{% static "js/offline-store.js" %}',
  '{% static "js/vendor/idb.umd.js" %}',
  '{% static "img/pwa/icon-192.png" %}',
  '{% static "img/logo.svg" %}',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.rtl.min.css',
];
const CDN_HOSTS = ['cdn.jsdelivr.net', 'fonts.googleapis.com', 'fonts.gstatic.com'];

async function precache() {
  const cache = await caches.open(CACHE);
  // One failing file (e.g. a CDN hiccup) must not stop the worker from installing.
  await Promise.all(PRECACHE.map(async (url) => {
    try {
      const cross = new URL(url, self.location.origin).origin !== self.location.origin;
      const res = await fetch(new Request(url, { cache: 'reload', mode: cross ? 'no-cors' : 'same-origin' }));
      if (res.ok || res.type === 'opaque') await cache.put(url, res);
    } catch (e) { /* ignore */ }
  }));
}

self.addEventListener('install', (event) => {
  // No skipWaiting here: the page shows "New version available" and asks us to take over (see the message handler).
  event.waitUntil(precache());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

function cacheable(url) {
  if (url.origin === self.location.origin) return url.pathname.startsWith('/static/');
  return CDN_HOSTS.includes(url.hostname);
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  const fresh = fetch(request).then((res) => {
    if (res.ok || res.type === 'opaque') cache.put(request, res.clone());
    return res;
  });
  if (cached) { fresh.catch(() => {}); return cached; }
  return fresh;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (request.mode === 'navigate') {
    if (url.origin !== self.location.origin) return;
    event.respondWith(fetch(request).catch(async () => (await caches.match(OFFLINE_URL)) || Response.error()));
    return;
  }
  if (cacheable(url)) event.respondWith(staleWhileRevalidate(request));
});

self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
  if (event.data === 'CLEAR_CACHES') {
    // On logout: drop everything stored here, then put back only the public shell.
    event.waitUntil(caches.keys().then((names) => Promise.all(names.map((n) => caches.delete(n)))).then(precache));
  }
});

// Daily reminder sent from the server (Web Push).
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data.json(); } catch (e) { /* no payload */ }
  event.waitUntil(self.registration.showNotification(data.title || 'KhorochAi', {
    body: data.body || '',
    icon: '{% static "img/pwa/icon-192.png" %}',
    tag: 'daily-reminder',
    data: { url: data.url || '/' },
  }));
});

// Opening a notification (debt reminder or daily reminder) takes you to the debt.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    const open = list.find((c) => 'focus' in c);
    if (open) return open.navigate(url).then((c) => (c || open).focus()).catch(() => self.clients.openWindow(url));
    return self.clients.openWindow(url);
  }));
});
