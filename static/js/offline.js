// The offline page: a small form to add an expense or income without internet. Entries wait on this device until they sync.
(function () {
  const store = window.KhorochOffline;
  const form = document.getElementById('offline-form');
  const unavailable = document.getElementById('offline-unavailable');
  const saved = document.getElementById('offline-saved');
  if (!form) return;

  async function init() {
    const snap = store && (await store.getSnapshot());
    if (!snap) { unavailable.hidden = false; return; }
    form.hidden = false;
    const $ = (name) => form.elements[name];

    const fillCategories = () => {
      const type = $('type').value;
      $('category').replaceChildren(new Option('—', ''));
      snap.categories.filter((c) => c.type === type).forEach((c) => $('category').add(new Option(c.label, c.id)));
    };
    $('account').replaceChildren(new Option('—', ''));
    snap.accounts.forEach((a) => $('account').add(new Option(a.label, a.id)));
    if (snap.default_account) $('account').value = snap.default_account;
    const today = new Date();
    $('date').value = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    fillCategories();
    form.querySelectorAll('[name=type]').forEach((r) => r.addEventListener('change', fillCategories));

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const amount = parseFloat($('amount').value);
      if (!(amount > 0)) { $('amount').focus(); return; }
      const label = (sel) => (sel.selectedIndex > 0 ? sel.options[sel.selectedIndex].text : '');
      await store.add({
        type: $('type').value, amount: amount.toFixed(2), note: $('note').value.trim().slice(0, 255), date: $('date').value,
        category: $('category').value, category_label: label($('category')),
        account: $('account').value, account_label: label($('account')),
      });
      $('amount').value = '';
      $('note').value = '';
      saved.hidden = false;
      setTimeout(() => { saved.hidden = true; }, 4000);
      $('amount').focus();
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  window.addEventListener('khoroch:synced', () => { document.getElementById('offline-synced').hidden = false; });
})();
