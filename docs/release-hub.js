const entries = [...document.querySelectorAll('.release-entry')];
const buttons = [...document.querySelectorAll('[data-filter]')];
let filter = 'all';
function applyFilter() {
  let count = 0;
  for (const entry of entries) {
    const matches = filter === 'all' || entry.dataset.kinds.split(' ').includes(filter);
    entry.hidden = !matches;
    if (matches) count++;
  }
  for (const button of buttons) {
    const selected = button.dataset.filter === filter;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  }
  document.querySelector('.result-count').textContent = `${count} ${count === 1 ? 'versão encontrada' : 'versões encontradas'}`;
  document.querySelector('.empty').hidden = count !== 0;
}
for (const button of buttons) button.addEventListener('click', () => {filter = button.dataset.filter; applyFilter();});
document.querySelector('#reset-filters').addEventListener('click', () => {filter = 'all'; applyFilter();});
for (const link of document.querySelectorAll('.version-sidebar nav a')) {
  link.addEventListener('click', () => {
    filter = 'all';applyFilter();
    for (const item of document.querySelectorAll('.version-sidebar nav a')) item.classList.toggle('current',item === link);
  });
}
applyFilter();
