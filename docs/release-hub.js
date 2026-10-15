const input = document.querySelector('#release-search');
const entries = [...document.querySelectorAll('.release-entry')];
const buttons = [...document.querySelectorAll('[data-filter]')];
let filter = 'all';
const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
function applyFilters() {
  const query = normalize(input.value.trim());
  let count = 0;
  for (const entry of entries) {
    const matches = (filter === 'all' || entry.dataset.kinds.split(' ').includes(filter)) && normalize(entry.textContent).includes(query);
    entry.hidden = !matches;
    if (matches) count++;
  }
  for (const button of buttons) {
    const selected = button.dataset.filter === filter;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  }
  document.querySelector('.result-count').textContent = `${count} ${count === 1 ? 'registro encontrado' : 'registros encontrados'}`;
  document.querySelector('.empty').hidden = count !== 0;
}
for (const button of buttons) button.addEventListener('click', () => {filter = button.dataset.filter; applyFilters();});
input.addEventListener('input', applyFilters);
document.querySelector('#reset-filters').addEventListener('click', () => {input.value = ''; filter = 'all'; applyFilters(); input.focus();});
for (const link of document.querySelectorAll('.version-sidebar nav a')) {
  link.addEventListener('click', () => {
    input.value = '';filter = 'all';applyFilters();
    for (const item of document.querySelectorAll('.version-sidebar nav a')) item.classList.toggle('current',item === link);
  });
}
applyFilters();
