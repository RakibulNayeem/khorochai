{% load static %}/* KhorochAi service worker, served at /sw.js.
 *
 * Its only job is notifications: the daily reminder (Web Push) and the debt reminders.
 * It does NOT cache pages or files and does not touch network requests, so the site always loads fresh.
 * When it replaces the old caching worker it also deletes every cache that worker left behind. */
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((names) => Promise.all(names.map((n) => caches.delete(n)))).then(() => self.clients.claim()));
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

// Opening a notification (debt reminder or daily reminder) takes you to the page it names.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    const open = list.find((c) => 'focus' in c);
    if (open) return open.navigate(url).then((c) => (c || open).focus()).catch(() => self.clients.openWindow(url));
    return self.clients.openWindow(url);
  }));
});
