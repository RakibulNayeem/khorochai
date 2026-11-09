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

  // A new deploy changes /sw.js. The new worker waits; we ask before switching so nobody loses a half-typed form.
  const toast = document.getElementById('update-toast');
  function offerUpdate(worker) {
    if (!toast) return;
    toast.hidden = false;
    toast.querySelector('[data-refresh]').onclick = () => worker.postMessage('SKIP_WAITING');
    toast.querySelector('.btn-close').onclick = () => { toast.hidden = true; };
  }
  let reloading = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloading || !hadController) return;   // first install also "changes" the controller: no reload then
    reloading = true;
    location.reload();
  });
  const hadController = !!navigator.serviceWorker.controller;

  navigator.serviceWorker.register('/sw.js', { scope: '/' }).then((reg) => {
    if (reg.waiting && navigator.serviceWorker.controller) offerUpdate(reg.waiting);
    reg.addEventListener('updatefound', () => {
      const worker = reg.installing;
      worker.addEventListener('statechange', () => {
        if (worker.state === 'installed' && navigator.serviceWorker.controller) offerUpdate(worker);
      });
    });
    // Long-open tabs (an installed app can stay open for days) re-check when they come back into view.
    document.addEventListener('visibilitychange', () => { if (!document.hidden) reg.update().catch(() => {}); });
  }).catch(() => { /* e.g. private mode: the site works without it */ });
})();
