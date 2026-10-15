const snapshot = 'befec33fd19d622f8144a92c13bfbae98b2309ff';
const repo = 'git+https://github.com/LuisCarlosLopes/codesteer-atlas.git';
const channel = document.querySelector('#release-channel');
const platform = document.querySelector('#platform');
let profile = 'compact';
let toastTimer;
function render() {
  const revision = channel.value === 'snapshot' ? snapshot : 'main';
  const source = `${repo}@${revision}`;
  document.querySelector('#backup-code').textContent = platform.value === 'unix'
    ? 'cp -R .code-index .code-index-backup-antes-v3'
    : 'Copy-Item -Recurse .code-index .code-index-backup-antes-v3';
  document.querySelector('#migration-code').textContent = `uvx --refresh --from ${source} atlas-index --workspace . --full`;
  document.querySelector('#channel-note').textContent = channel.value === 'snapshot'
    ? 'Snapshot fixo da análise: CLI e MCP usam exatamente befec33. Não depende de a branch já ter sido mesclada.'
    : 'Use esta opção apenas depois de confirmar que a main contém as mudanças. Na data desta análise, a main ainda declara 2.1.1.';
  const env = {ATLAS_INDEX_DIR: '${workspaceFolder}/.code-index'};
  env.ATLAS_CONTEXT_OPTIMIZATION = profile === 'basic' ? '0' : '1';
  env.ATLAS_OBSERVABILITY = profile === 'basic' ? '0' : '1';
  env.ATLAS_RELEVANCE = profile === 'jev' ? '1' : '0';
  if (profile === 'jev') {
    env.ATLAS_RELEVANCE_API_URL = 'https://openrouter.ai/api/v1/systemone';
    env.ATLAS_RELEVANCE_API_KEY = 'SUA_CHAVE_OPENROUTER';
  }
  env.ATLAS_SEMANTIC = '0';
  const configuration = {mcpServers: {'codesteer-atlas': {command: 'uvx', args: ['--refresh', '--from', source, 'atlas-serve'], env}}};
  document.querySelector('#config-code').textContent = JSON.stringify(configuration, null, 2);
  document.querySelector('#config-revision').textContent = channel.value === 'snapshot' ? 'befec33 · snapshot' : 'main · após merge';
  const descriptions = {
    compact: 'Compactação e observabilidade ligadas. Jev e semântica desligados. A primeira preparação pode baixar dependências e o modelo; depois a busca é local.',
    jev: 'Compactação + rubrica Jev v2 com corte padrão. Consulta e candidatos seguem ao OpenRouter com custo de API. Gate desligado por padrão.',
    basic: 'Busca local, sem compactação nem métricas ativadas por este exemplo. Jev e semântica desligados.'
  };
  document.querySelector('#profile-description').textContent = descriptions[profile];
  for (const button of document.querySelectorAll('[data-profile]')) {
    const active = button.dataset.profile === profile;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  }
}
for (const button of document.querySelectorAll('[data-profile]')) {
  button.addEventListener('click', () => {profile = button.dataset.profile; render();});
}
for (const button of document.querySelectorAll('[data-copy]')) {
  button.addEventListener('click', async () => {
    const text = document.getElementById(button.dataset.copy).textContent;
    const toast = document.querySelector('.toast');
    try {
      await navigator.clipboard.writeText(text);
      toast.textContent = 'Copiado para a área de transferência.';
    } catch {
      const range = document.createRange();
      range.selectNodeContents(document.getElementById(button.dataset.copy));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      toast.textContent = 'Texto selecionado. Use Ctrl+C ou ⌘C para copiar.';
    }
    toast.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
  });
}
for (const link of document.querySelectorAll('[data-source]')) {
  link.href = `https://github.com/LuisCarlosLopes/codesteer-atlas/blob/${snapshot}/${link.dataset.source}`;
  link.target = '_blank';
  link.rel = 'noopener';
}
channel.addEventListener('change', render);
platform.addEventListener('change', render);
render();
