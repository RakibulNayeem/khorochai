// Makes the site installable as an app.
// - Registers the service worker (Chrome/Edge/Android need one before they offer "Install").
// - Android/desktop Chrome: keeps the browser's install prompt and shows the "Install app" button (data-install-btn).
// - Already running as an installed app: shows "App installed" (data-install-done) instead.
// iPhone and iPad have no install prompt, so the install page lists the Share → Add to Home Screen steps.
(function () {
  const me = document.currentScript;
  if ('serviceWorker' in navigator && me && me.dataset.sw) {
    navigator.serviceWorker.register(me.dataset.sw).catch(() => { /* private mode or blocked: the site still works */ });
  }

  const buttons = document.querySelectorAll('[data-install-btn]');
  const done = document.querySelectorAll('[data-install-done]');
  if (!buttons.length && !done.length) return;

  const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const show = (nodes, on) => nodes.forEach((n) => { n.hidden = !on; });
  let deferred = null;

  const refresh = () => {
    show(done, standalone);
    show(buttons, !standalone && !!deferred);
  };
  refresh();

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();  // keep it for our own button
    deferred = e;
    refresh();
  });
  window.addEventListener('appinstalled', () => {
    deferred = null;
    show(buttons, false);
    show(done, true);
  });
  buttons.forEach((b) => b.addEventListener('click', () => {
    if (!deferred) return;
    deferred.prompt();
    deferred.userChoice.finally(() => { deferred = null; refresh(); });
  }));
})();
