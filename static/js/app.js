// Site-wide basics: the notification worker, and hiding the "Download Android app" buttons inside the app itself.
(function () {
  // Inside the Android app (it reports an android-app:// referrer and runs standalone) the download buttons make no sense.
  const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  if (standalone || document.referrer.startsWith('android-app://')) {
    document.querySelectorAll('[data-android-download]').forEach((el) => { el.hidden = true; });
  }

  // Reminders (the daily one and the debt ones) show notifications through this worker.
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});

  // Earlier versions kept a queue of offline entries in the browser. That feature is gone: tidy up what it left.
  try { indexedDB.deleteDatabase('khorochai-offline'); } catch (e) { /* not available */ }
})();
