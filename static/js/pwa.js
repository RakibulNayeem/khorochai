// Registers the service worker, shows the offline banner, and clears the worker's caches on logout.
(function () {
  const banner = document.getElementById('offline-banner');
  const sync = () => { if (banner) banner.hidden = navigator.onLine; };
  window.addEventListener('online', sync);
  window.addEventListener('offline', sync);
  sync();

  if (!('serviceWorker' in navigator)) return;

  // Logout: empty the worker's caches (it re-adds only the public offline page).
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (form.tagName === 'FORM' && /\/auth\/logout\/?$/.test(new URL(form.action, location.href).pathname)) {
      if (navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage('CLEAR_CACHES');
      document.dispatchEvent(new Event('khoroch:logout'));
    }
  }, true);

  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => { /* e.g. private mode: the site works without it */ });
})();
