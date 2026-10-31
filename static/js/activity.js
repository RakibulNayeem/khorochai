// Counts visits and time spent for the admin panel: a page view, then the seconds this tab was really in use.
// Sends only those numbers and a random ID for this browser, never anything from the page.
(function () {
  const script = document.currentScript;
  const url = script && script.dataset.url;
  if (!url) return;

  const TICK = 5000;
  const SEND_EVERY = 60;   // seconds
  const IDLE_AFTER = 180000; // no touch, key or scroll for 3 minutes: the time stops counting

  function browserId() {
    const make = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
    try {
      let id = localStorage.getItem('khoroch_visitor');
      if (!/^[a-f0-9]{32}$/.test(id || '')) {
        id = make();
        localStorage.setItem('khoroch_visitor', id);
      }
      return id;
    } catch (e) {
      return make(); // storage blocked: this page view still counts
    }
  }

  const visitor = browserId();
  let seconds = 0;
  let lastTick = Date.now();
  let lastInput = Date.now();

  function send(view) {
    const whole = Math.floor(seconds);
    if (!view && !whole) return;
    seconds -= whole;
    const body = new URLSearchParams({ visitor, seconds: whole, view: view ? 1 : 0 });
    if (!navigator.sendBeacon || !navigator.sendBeacon(url, body)) {
      fetch(url, { method: 'POST', body, keepalive: true }).catch(() => {});
    }
  }

  function tick() {
    const now = Date.now();
    // a sleeping phone or throttled tab can leave a long gap: that isn't time spent
    const gap = Math.min(now - lastTick, TICK * 2);
    lastTick = now;
    if (document.visibilityState === 'visible' && now - lastInput < IDLE_AFTER) seconds += gap / 1000;
    if (seconds >= SEND_EVERY) send(false);
  }

  ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach((name) => {
    addEventListener(name, () => { lastInput = Date.now(); }, { passive: true, capture: true });
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') { tick(); send(false); } else { lastTick = lastInput = Date.now(); }
  });
  addEventListener('pagehide', () => { tick(); send(false); });
  addEventListener('pageshow', () => { lastTick = lastInput = Date.now(); });

  send(true);
  setInterval(tick, TICK);
})();
