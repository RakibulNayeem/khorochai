// Settings: the daily reminder switch. Permission is asked only when the user turns the switch on (never on page load).
(function () {
  const box = document.getElementById('daily-reminder');
  if (!box) return;
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const toggle = document.getElementById('reminder-toggle');
  const time = document.getElementById('reminder-time');
  const status = document.getElementById('reminder-status');
  const say = (text, bad) => { status.textContent = text; status.className = 'small mt-2 ' + (bad ? 'text-danger' : 'text-muted'); };
  const csrf = () => { const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ''; };
  const post = (url, body) => fetch(url, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(body),
  }).then((r) => { if (!r.ok) throw new Error(r.status); return r; });
  const keyBytes = (b64) => Uint8Array.from(atob(b64.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (b64.length % 4)) % 4)), (c) => c.charCodeAt(0));

  if (!supported) {   // iPhone Safari outside the installed app has no push at all: explain how to get it
    if (ios) { box.hidden = false; toggle.disabled = time.disabled = true; say(box.dataset.msgIos, true); }
    return;
  }
  box.hidden = false;

  const subscription = () => navigator.serviceWorker.ready.then((reg) => reg.pushManager.getSubscription());
  // The switch shows "on" only when the account has it on AND this device is subscribed.
  subscription().then((sub) => { toggle.checked = box.dataset.enabled === '1' && !!sub; }).catch(() => {});

  async function enable() {
    if (Notification.permission === 'denied') throw new Error('denied');
    if (Notification.permission !== 'granted' && (await Notification.requestPermission()) !== 'granted') throw new Error('denied');
    const reg = await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription())
      || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(box.dataset.key) }));
    await post(box.dataset.subscribeUrl, { subscription: sub.toJSON(), time: time.value || '21:00' });
  }

  async function disable() {
    const sub = await subscription();
    await post(box.dataset.unsubscribeUrl, { endpoint: sub ? sub.endpoint : null });
    if (sub) await sub.unsubscribe();
  }

  toggle.addEventListener('change', async () => {
    toggle.disabled = true;
    try {
      if (toggle.checked) await enable(); else await disable();
      box.dataset.enabled = toggle.checked ? '1' : '0';
      say(toggle.checked ? box.dataset.msgOn : box.dataset.msgOff);
    } catch (e) {
      toggle.checked = !toggle.checked;
      say(e.message === 'denied' ? box.dataset.msgDenied : box.dataset.msgError, true);
    } finally { toggle.disabled = false; }
  });

  time.addEventListener('change', async () => {   // a new time takes effect right away when the reminder is on
    if (!toggle.checked || !time.value) return;
    try { await enable(); say(box.dataset.msgOn); } catch (e) { say(box.dataset.msgError, true); }
  });
})();
