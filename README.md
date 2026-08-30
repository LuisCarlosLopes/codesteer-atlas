# CodeSteer Atlas

Servidor MCP local para busca semântica em código. Usa Tree-sitter (AST), embeddings locais (`fastembed`/ONNX) e LanceDB. Tudo roda **100% offline** — o código-fonte nunca sai da sua máquina.

### Documentação

| Recurso | Descrição |
| -------- | ---------- |
| 📖 [Documentação visual](https://luiscarloslopes.github.io/codesteer-atlas/) | Conceitos MCP, busca híbrida e indexação |
| 📘 [Guia didático — Indexação, Grafo e MCP](docs/guia-indexacao-grafo-mcp.md) | Pipeline, diagramas, multi-repo e `graph.html` |

## Funcionalidades

- **Indexação por AST (Tree-sitter)**: chunks por classe/função/método, não por blocos arbitrários de linhas.
- **Busca híbrida**: similaridade vetorial + BM25, fundidas via RRF.
- **Indexação incremental**: só arquivos novos/alterados (hash sha256).
- **Embeddings locais**: `all-MiniLM-L6-v2` (384 dims) via `fastembed`, com lazy loading.
- **Grafo de conhecimento**: `.code-index/graph.json` + visualizador `graph.html` (abre via `file://`).
- **Rationale em código**: `NOTE`/`WHY`, cites `DEC`/`ADR`/`RFC` e wikilinks nos resultados de busca.
- **Multi-linguagem**: Python, JS/TS, Go, Java, C#, Dart, Pascal, VB6, Razor, XML, Markdown e mais.

## Começar (3 passos)

Pré-requisitos: Python 3.11–3.13 e [uv](https://github.com/astral-sh/uv) (fornece o `uvx`).

Você **não precisa clonar** este repositório para usar o Atlas. O índice fica em `.code-index/` na **raiz do seu projeto** (adicione essa pasta ao `.gitignore`).

### 1. Conectar o MCP no seu projeto

> **Importante — não instale o plugin em escopo global (user).**
>
> Plugins/MCP globais costumam iniciar o servidor com CWD = `$HOME`, sem a raiz do projeto aberto. Nesse caso o Atlas **não consegue inferir** de forma confiável a pasta `.code-index` do workspace (e pode criar ou achar um índice no lugar errado).
>
> Use sempre uma destas opções:
>
> 1. **Plugin no projeto atual** (escopo *project* ou *local*), ou
> 2. **Configuração manual** via `mcp.json` / `.mcp.json` **na raiz do projeto**.

#### Opção A — Plugin no projeto atual (Claude Code)

```text
/plugin marketplace add LuisCarlosLopes/codesteer-atlas
# ou pasta local: /plugin marketplace add /caminho/para/codesteer-atlas

/plugin install codesteer-atlas
```

Quando o Claude Code pedir o escopo, escolha **Project** (compartilhado no repo) ou **Local** (só neste workspace). **Não escolha User.**

Pela CLI:

```bash
claude plugin install codesteer-atlas --scope project
# ou: --scope local
```

#### Opção B — `mcp.json` manual (recomendado para Cursor, VS Code, Kiro, OpenCode…)

Copie o manifest **para a raiz do seu projeto** (não para a config global do editor) e reinicie o cliente:

| Cliente | Copiar de | Para |
| ------- | --------- | ---- |
| Cursor | [`examples/clients/cursor/mcp.json`](examples/clients/cursor/mcp.json) | `.cursor/mcp.json` |
| GitHub Copilot (VS Code) | [`examples/clients/vscode/mcp.json`](examples/clients/vscode/mcp.json) | `.vscode/mcp.json` |
| Kiro | [`examples/clients/kiro/settings/mcp.json`](examples/clients/kiro/settings/mcp.json) | `.kiro/settings/mcp.json` |
| OpenCode | [`examples/clients/opencode/opencode.json`](examples/clients/opencode/opencode.json) | `opencode.json` |
| Claude Code | [`.mcp.json`](.mcp.json) | `.mcp.json` na raiz do projeto |

Exemplo mínimo (Claude Code / vários clientes com chave `mcpServers`):

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
        "atlas-serve"
      ]
    }
  }
}
```

Detalhes por cliente e modo instalado (`uv tool install`): [`examples/clients/`](examples/clients/) e [CONTRIBUTING.md](CONTRIBUTING.md#configuração-manual-em-outros-clientes).

#### Outros canais (também por projeto)

- **Kiro Power**: Add Custom Power → Import from GitHub → `https://github.com/LuisCarlosLopes/codesteer-atlas.git`, e associe ao workspace atual.
- **Copilot CLI plugin**: prefira instalar no contexto do repositório em que você vai trabalhar; se o índice não for encontrado, use a Opção B (`.vscode/mcp.json` ou equivalente).

```bash
copilot plugin install LuisCarlosLopes/codesteer-atlas
```

### 2. Indexar o projeto

Na raiz do **seu** projeto (não do repositório do Atlas, a menos que seja esse o alvo):

```bash
cd /caminho/para/seu-projeto

# Uma vez: instala atlas-index / atlas-serve no PATH
uv tool install git+https://github.com/LuisCarlosLopes/codesteer-atlas.git

atlas-index --workspace .
```

Sem instalar no PATH (baixa o pacote a cada execução):

```bash
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .
```

Ao terminar: mensagem `Indexação Concluída com Sucesso!` e pasta `.code-index/` com `manifest.json`, `lancedb/`, `graph.json` e `graph.html`.

Atualizar o Atlas depois: `uv tool upgrade codesteer-atlas`.

### 3. Usar

Com o MCP conectado e o índice criado, o agente passa a ter as tools `atlas_*`. Nas próximas vezes:

```bash
atlas-index --workspace .            # incremental (padrão)
atlas-index --workspace . --full     # rebuild completo
atlas-index --workspace . --paths src --paths docs
```

Ou peça ao agente para usar a tool `atlas_index`.

> **Reindex automático:** ao iniciar o `atlas-serve` (abrir/reiniciar o editor), se `.code-index/` já existir, roda uma reindexação incremental em background. A **primeira** indexação (passo 2) continua manual. Log: `.code-index/background_reindex.log`.

## Uso

| Tool | Descrição |
|---|---|
| `atlas_search` | Busca híbrida. Por padrão retorna só metadados; use `include_content=true` ou `Read` nas linhas. Filtros: `repo`, `language`, `path_prefix`. |
| `atlas_brief` | Briefing do projeto (identidade, camadas, entrypoints, hubs). Chame primeiro em projeto desconhecido. `level=0` ou `1`. |
| `atlas_context` | Pacote da tarefa (`target` + `intent`: `edit`/`debug`/`review`/`understand`) numa chamada, com teto de tokens. |
| `atlas_graph` | Grafo: `hubs`, `path`, `explain`, `affected`. |
| `atlas_index` | Indexa/reindexa; regenera `graph.json` / `graph.html`. Suporta `dry_run`. |
| `atlas_status` | Diagnóstico do índice (`is_stale`, `graph_available`, `index_resolution`, …). |

Recurso somente leitura: `atlas://status`.

Após indexar, abra `.code-index/graph.html` no navegador (`file://`) para inspecionar o grafo.

### Rationale e grafo

Em resultados de código, `atlas_search` pode incluir `rationale_refs` (`DECISAO-002`, `ADR-001`, `[[wikilinks]]`, `# NOTE:` / `# WHY:`).

```text
atlas_graph(mode="hubs", top_n=10)
atlas_graph(mode="path", source="src/app.py", target="dec-002")
atlas_graph(mode="explain", target="AuthService.login")
atlas_graph(mode="affected", target="AuthService.login")
atlas_context(target="AuthService.login", intent="edit")
```

> **Upgrade:** `atlas_graph` / `graph.html` exigem reindex em índices antigos (&lt; `2.1.0`).

## Instruções para agentes de IA (AGENTS.md / CLAUDE.md)

Copie o bloco abaixo para as instruções do seu projeto:

| Cliente / IDE | Arquivo |
|---|---|
| Cursor, Copilot (VS Code), genérico | [`AGENTS.md`](AGENTS.md) |
| Claude Code | [`CLAUDE.md`](CLAUDE.md) |
| Kiro | regras do Power / instruções do agente |
| GitHub Copilot CLI | instruções do plugin ou regras do projeto |

```markdown
# Busca de código com `codesteer-atlas`

Este repositório é indexado pelo MCP `codesteer-atlas`. Para entender, planejar, pesquisar ou explorar código, use Atlas antes de `grep`, `rg`, `find`, glob ou leitura em massa.

## Use assim

- `atlas_brief`: orientar-se num projeto desconhecido — chame primeiro, uma vez
- `atlas_context`: quando o símbolo/arquivo da tarefa já é conhecido (`intent` = edit/debug/review/understand)
- `atlas_search`: localizar função, classe, método, símbolo ou conceito
- `atlas_graph`: hubs, paths, conexões e `mode="affected"` (raio de impacto)
- `atlas_status`: só se houver suspeita de índice ausente ou desatualizado
- `atlas_index`: reindexar após mudanças grandes ou índice stale

## Fluxo padrão

1. `atlas_search` para descoberta (metadados).
2. Restrinja com `path_prefix` e `language` quando fizer sentido.
3. Leia os hits com `Read`, ou repita com `include_content=true`.

## Quando pode pular o Atlas

- o usuário já informou o caminho exato
- confirmação de string literal exata
- edição, diff, commit, git, CI, testes ou instalação de deps
- MCP indisponível ou índice vazio/desatualizado

## Índice desatualizado

1. `atlas_status`
2. Se necessário, `atlas_index`
3. Fallback local só se o problema persistir
```

## Como funciona

O Atlas divide cada arquivo em `CodeChunk`s no nível de símbolo via Tree-sitter, gera embeddings locais e indexa em LanceDB (vetorial + BM25 / RRF):

```
src/auth/service.py
  ├── class AuthService          (linhas 10–45)
  ├── AuthService.login          (linhas 20–35)
  └── AuthService.logout         (linhas 37–44)
```

Detalhes do pipeline: [CONTRIBUTING.md](CONTRIBUTING.md#pipeline-de-indexação-detalhado).

### Excluindo arquivos com `.atlasignore`

Na raiz do workspace (sintaxe igual à do `.gitignore`):

```gitignore
*.log
fixtures/
/dist
**/*.generated.py
!important.log
```

É um filtro **adicional** — `.git`, `node_modules`, `.venv`, `__pycache__` e `.code-index` continuam sempre ignorados.

## Onde fica o `.code-index`?

Ordem de resolução:

1. `--index-dir` (CLI)
2. `ATLAS_INDEX_DIR` (env)
3. Busca ascendente a partir do CWD
4. Busca a partir da raiz do editor (`CLAUDE_PROJECT_DIR`, `WORKSPACE_FOLDER_PATHS`)
5. Fallback `.code-index` relativo à raiz conhecida (ou ao CWD)

Com MCP ligado **ao projeto** (plugin project/local ou `mcp.json` na raiz), o item 3 ou 4 costuma bastar após `atlas-index --workspace .`.

Se o servidor nascer com CWD errado (caso típico de instalação **global**), o Atlas tenta recuperar via MCP `roots/list` quando o cliente suporta. Mesmo assim, **prefira instalação por projeto** — é o caminho estável.

Para forçar um caminho explícito no `mcp.json` do projeto:

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
        "atlas-serve"
      ],
      "env": {
        "ATLAS_INDEX_DIR": "${workspaceFolder}/.code-index"
      }
    }
  }
}
```

> No Cursor, `${workspaceFolder}` é a forma mais segura de amarrar o índice ao projeto aberto. Veja [CONTRIBUTING.md — Cursor](CONTRIBUTING.md#cursor).

Diagnóstico: `atlas_status` → `index_resolution`.

## Contribuindo

Clonar o repo, testes, lint e configuração avançada: [CONTRIBUTING.md](CONTRIBUTING.md) e [CLAUDE.md](CLAUDE.md).

## Licença

Veja [LICENSE](LICENSE).
