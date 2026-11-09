// Debt reminders as real notifications on this device (phone or computer), once the user allows them.
// Each reminder is shown once a day, when a page of the site is opened. The bell menu always lists them.
(function () {
  const dataEl = document.getElementById('reminder-data');
  const button = document.getElementById('notify-on');
  if (!dataEl || !('Notification' in window)) return;
  const reminders = JSON.parse(dataEl.textContent);
  const bn = document.documentElement.lang === 'bn';
  const digits = (s) => (bn ? s.replace(/[0-9]/g, (d) => '০১২৩৪৫৬৭৮৯'[d]) : s);
  const icon = document.querySelector('link[rel="apple-touch-icon"]').href;

  const seen = () => { try { return localStorage.getItem('khoroch_reminded') || ''; } catch (e) { return ''; } };
  const remember = (ids) => { try { localStorage.setItem('khoroch_reminded', ids.join(',')); } catch (e) { /* private mode */ } };

  function registration() {
    // Android only shows notifications through a service worker; desktop browsers can use either.
    if (!('serviceWorker' in navigator)) return Promise.resolve(null);
    // The site's worker (/sw.js, registered by pwa.js on every page) controls the whole site.
    return navigator.serviceWorker.ready.catch(() => null);
  }

  function showDue() {
    const shown = seen().split(',');
    const fresh = reminders.filter((r) => !shown.includes(r.id));
    if (!fresh.length) return;
    registration().then((reg) => {
      fresh.forEach((r) => {
        const options = { body: digits(r.body), icon: icon, tag: r.id, data: { url: r.url } };
        if (reg) reg.showNotification(digits(r.title), options);
        else new Notification(digits(r.title), options).onclick = () => { window.focus(); location.href = r.url; };
      });
      remember(reminders.map((r) => r.id));  // keeps only today's ids
    });
  }

  if (Notification.permission === 'granted') showDue();
  else if (Notification.permission === 'default' && button) {
    button.hidden = false;
    button.addEventListener('click', () => Notification.requestPermission().then((p) => {
      button.hidden = true;
      if (p === 'granted') showDue();
    }));
  }
})();
