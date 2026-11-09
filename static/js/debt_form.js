// Add/edit debt: "N days from today" under the due date, and the reminder options only when a due date is set.
(function () {
  const due = document.getElementById('id_due_date');
  const hint = document.getElementById('due-hint');
  const custom = document.getElementById('remind-custom');
  const when = document.getElementById('remind-when');
  const chips = document.querySelector('.remind-chips');
  const needsDate = document.getElementById('remind-needs-date');
  const save = document.getElementById('save-label');
  if (!due || !hint) return;

  function daysAway() {
    if (!due.value) return null;
    const [y, m, d] = due.value.split('-').map(Number);
    const now = new Date();
    const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.round((Date.UTC(y, m - 1, d) - today) / 86400000);
  }

  function update() {
    const n = daysAway();
    const remind = (document.querySelector('input[name="remind"]:checked') || {}).value || 'off';
    if (n === null) hint.textContent = '';
    else if (n < 0) hint.textContent = hint.dataset.past;
    else if (n === 0) hint.textContent = hint.dataset.today;
    else if (n === 1) hint.textContent = hint.dataset.one;
    else hint.textContent = hint.dataset.many.replace('N', n);

    const hasDate = n !== null;
    chips.hidden = !hasDate;
    needsDate.hidden = hasDate;
    custom.hidden = !hasDate || remind !== 'custom';
    when.hidden = !hasDate || remind === 'off';
    save.textContent = hasDate && remind !== 'off' ? save.dataset.remind : save.dataset.plain;
  }

  due.addEventListener('input', update);
  due.addEventListener('change', update);
  document.querySelectorAll('input[name="remind"]').forEach((r) => r.addEventListener('change', () => {
    update();
    if (r.checked && r.value === 'custom') document.getElementById('id_remind_custom').focus();
  }));
  update();
})();
