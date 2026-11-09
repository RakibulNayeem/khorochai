// Entries typed while offline: saved in IndexedDB (via idb) as "pending", sent to the server when the connection is back.
//
// - Every entry gets its own UUID (client_id). The server treats a repeated client_id as "already saved",
//   so a retry after a dropped connection can never create the same entry twice.
// - Entries belong to the user who typed them and are only sent while that same user is logged in.
// - Logging out removes the saved categories/accounts but keeps unsent entries, so nothing typed is lost.
(function () {
  if (!window.idb || !window.indexedDB) { window.KhorochOffline = null; return; }

  const dbPromise = idb.openDB('khorochai-offline', 1, {
    upgrade(db) {
      db.createObjectStore('pending', { keyPath: 'client_id' });
      db.createObjectStore('meta');
    },
  });
  const changed = () => window.dispatchEvent(new Event('khoroch:offline-change'));
  const newId = () => (crypto.randomUUID ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 3) | 8).toString(16);
    }));
  const csrf = () => { const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ''; };

  async function getSnapshot() { return (await dbPromise).get('meta', 'snapshot'); }
  async function clearSnapshot() { await (await dbPromise).delete('meta', 'snapshot'); }

  // Saves the user's categories and accounts so the offline form can offer them.
  async function refreshSnapshot() {
    if (!navigator.onLine) return;
    try {
      const res = await fetch('/transactions/offline-data/', { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (res.status === 401) return clearSnapshot();
      if (res.ok) await (await dbPromise).put('meta', await res.json(), 'snapshot');
    } catch (e) { /* offline or server down: keep what we have */ }
  }

  async function list() {
    const snap = await getSnapshot();
    if (!snap) return [];
    const all = await (await dbPromise).getAll('pending');
    return all.filter((e) => e.user === snap.user).sort((a, b) => a.created - b.created);
  }

  async function add(entry) {
    const snap = await getSnapshot();
    if (!snap) throw new Error('no snapshot');
    const row = Object.assign({ client_id: newId(), user: snap.user, created: Date.now(), status: 'pending', attempts: 0 }, entry);
    await (await dbPromise).put('pending', row);
    changed();
    sync();
    return row;
  }

  async function remove(id) { await (await dbPromise).delete('pending', id); changed(); }

  let timer = null;
  let running = false;
  const backoff = (attempts) => Math.min(5 * 60000, 5000 * 2 ** attempts);

  async function sync() {
    if (running || !navigator.onLine) return;
    running = true;
    clearTimeout(timer);
    let retry = 0;
    try {
      const db = await dbPromise;
      const todo = (await list()).filter((e) => e.status === 'pending').slice(0, 50);
      if (!todo.length) return;
      const body = todo.map(({ client_id, type, amount, category, account, note, date }) =>
        ({ client_id, type, amount, category, account, note, date }));
      let results = null;
      try {
        const res = await fetch('/transactions/sync/', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
          body: JSON.stringify({ entries: body }),
        });
        if (res.ok) results = (await res.json()).results;
      } catch (e) { /* network error: retry below */ }

      if (!results) {                       // offline, logged out, CSRF expired or server error: try again later
        for (const e of todo) { e.attempts += 1; await db.put('pending', e); }
        retry = backoff(Math.max(...todo.map((e) => e.attempts)));
      } else {
        const byId = new Map(results.map((r) => [r.client_id, r]));
        for (const e of todo) {
          const r = byId.get(e.client_id);
          if (r && r.status === 'synced') await db.delete('pending', e.client_id);
          else if (r && r.status === 'invalid') { e.status = 'failed'; e.error = r.error || ''; await db.put('pending', e); }
          else { e.attempts += 1; await db.put('pending', e); retry = backoff(e.attempts); }
        }
        if ((await list()).some((e) => e.status === 'pending') && !retry) retry = 1000; // more than one batch
        const synced = results.filter((r) => r.status === 'synced').length;
        if (synced) window.dispatchEvent(new CustomEvent('khoroch:synced', { detail: synced }));
      }
      changed();
    } finally {
      running = false;
      if (retry) timer = setTimeout(sync, retry);
    }
  }

  // ---- A small list of the entries that aren't saved on the server yet (works on any page that has [data-pending-list]).
  function mountList(box) {
    const t = box.dataset;
    async function render() {
      const items = await list();
      box.replaceChildren();
      box.hidden = !items.length;
      if (!items.length) return;
      const wrap = document.createElement('div');
      wrap.className = 'pending-card';
      for (const e of items) {
        const row = document.createElement('div');
        row.className = 'pending-row';
        const main = document.createElement('div');
        main.className = 'pending-main';
        const what = document.createElement('div');
        what.textContent = [e.date, e.category_label || '', e.note || ''].filter(Boolean).join(' · ');
        const sub = document.createElement('div');
        sub.className = 'small text-muted';
        sub.textContent = e.status === 'failed' ? (e.error || t.failed) : (e.account_label || '');
        main.append(what, sub);
        const amount = document.createElement('strong');
        amount.className = e.type === 'income' ? 'text-income' : 'text-expense';
        amount.textContent = (e.type === 'income' ? '+' : '−') + e.amount;
        const badge = document.createElement('span');
        badge.className = 'badge ' + (e.status === 'failed' ? 'text-bg-danger' : 'text-bg-warning');
        badge.textContent = e.status === 'failed' ? t.failed : t.notSynced;
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'btn btn-sm btn-link text-danger px-1';
        del.textContent = t.delete;
        del.addEventListener('click', () => remove(e.client_id));
        row.append(main, amount, badge, del);
        wrap.append(row);
      }
      box.append(wrap);
    }
    window.addEventListener('khoroch:offline-change', render);
    render();
  }

  // Start-up: remember the user's categories/accounts, send anything waiting, and show pending entries.
  document.addEventListener('khoroch:logout', clearSnapshot);
  window.addEventListener('online', sync);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
  window.KhorochOffline = { add, list, remove, sync, getSnapshot, refreshSnapshot, mountList };

  const loggedOut = document.currentScript && document.currentScript.dataset.auth === '0';
  document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('[data-pending-list]').forEach(mountList);
    if (loggedOut) return clearSnapshot();   // any page seen while logged out removes the saved categories/accounts
    await refreshSnapshot();
    sync();
  });
})();
