// Add/edit account: search a list of banks or mobile services for the chosen type.
// Picking one only fills in the name, icon and color, and the name can still be typed or changed by hand.
(function () {
  const picker = document.getElementById('account-picker');
  const dataEl = document.getElementById('account-picker-data');
  const kind = document.getElementById('id_kind');
  const name = document.getElementById('id_name');
  const icon = document.getElementById('id_icon');
  const color = document.getElementById('id_color');
  if (!picker || !dataEl || !kind || !name) return;

  const { lists, defaults } = JSON.parse(dataEl.textContent);
  const search = document.getElementById('account-picker-q');
  const list = document.getElementById('account-picker-list');
  let lastKind = kind.value;

  function show(open) {
    const items = lists[kind.value] || [];
    list.textContent = '';
    if (!open) { list.hidden = true; return; }
    const q = search.value.trim().toLowerCase();
    const found = items.filter((p) => q.split(/\s+/).every((word) => p.search.includes(word)));
    found.forEach((p) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'picker-row';
      row.textContent = `${p.icon} ${p.name}`;
      row.addEventListener('click', () => choose(p));
      list.appendChild(row);
    });
    list.hidden = !found.length;
  }

  function choose(p) {
    name.value = p.name;
    icon.value = p.icon;
    color.value = p.color;
    search.value = '';
    show(false);
  }

  function kindChanged() {
    // an icon or color the user chose stays; only the previous type's stock ones follow the new type
    const before = defaults[lastKind];
    const after = defaults[kind.value];
    if (before && after) {
      if (icon.value === before.icon) icon.value = after.icon;
      if (color.value.toLowerCase() === before.color) color.value = after.color;
    }
    lastKind = kind.value;
    search.value = '';
    show(false);
    picker.hidden = !lists[kind.value];
  }

  search.addEventListener('focus', () => show(true));
  search.addEventListener('input', () => show(true));
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') show(false);
    if (e.key !== 'Enter') return;
    e.preventDefault(); // Enter here picks the top match and doesn't save the form
    const first = list.querySelector('.picker-row');
    if (first && !list.hidden) first.click();
  });
  document.addEventListener('click', (e) => { if (!picker.contains(e.target)) show(false); });
  kind.addEventListener('change', kindChanged);

  picker.hidden = !lists[kind.value];
})();
