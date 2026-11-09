// "Install KhorochAi" buttons: Android/desktop use the browser's install prompt; iPhone gets a Share -> Add to Home Screen hint.
(function () {
  const buttons = document.querySelectorAll('[data-pwa-install]');
  const hint = document.getElementById('pwa-ios-hint');
  const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let deferred = null;

  const show = (on) => buttons.forEach((b) => { b.hidden = !on; });
  if (standalone) return;

  if (ios) {
    show(true);
    buttons.forEach((b) => b.addEventListener('click', () => { if (hint) hint.hidden = false; }));
    if (hint) hint.querySelector('.btn-close').addEventListener('click', () => { hint.hidden = true; });
    return;
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferred = e;
    show(true);
  });
  buttons.forEach((b) => b.addEventListener('click', async () => {
    if (!deferred) return;
    deferred.prompt();
    await deferred.userChoice;
    deferred = null;
    show(false);
  }));
  window.addEventListener('appinstalled', () => { deferred = null; show(false); });
})();
