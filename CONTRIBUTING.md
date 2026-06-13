# Contribuindo com o CodeSteer Atlas

Este documento é para quem vai **clonar o repositório**, desenvolver o Atlas, rodar testes/lint, ou configurar manualmente o servidor MCP em clientes não cobertos pelos manifests prontos do [README](README.md).

Se você só quer **usar** o Atlas para buscar em outro projeto, não precisa de nada daqui — veja o [README](README.md).

## Setup de desenvolvimento

```bash
git clone https://github.com/LuisCarlosLopes/codesteer-atlas.git
cd codesteer-atlas

# macOS / Linux
./setup.sh

# Windows (PowerShell)
.\setup.ps1
```

O script verifica o `uv`, sincroniza as dependências (`uv sync --group dev`) e valida os imports críticos via `deploy_mcp.py --check`.

### Indexar outro projeto a partir do clone

```bash
uv --directory /caminho/para/codesteer-atlas run atlas-index --workspace /caminho/para/seu-projeto
# ou, estando dentro do clone do Atlas com o projeto como alvo:
uv run atlas-index --workspace /caminho/para/seu-projeto
```

## Rodar o servidor MCP localmente

```bash
uv run atlas-serve

# Apontando para um diretório de índice específico
uv run atlas-serve --index-dir /caminho/para/.code-index
```

O servidor se comunica via stdio (JSON-RPC), pronto para ser usado por clientes MCP (Claude Code, Claude Desktop, Cursor, Cline, etc.).

## Testes e lint

```bash
# Rodar testes
uv run pytest -v

# Rodar um teste específico
uv run pytest tests/test_indexer.py::test_name

# Rodar lint
uv run ruff check
```

Qualquer mudança de lógica no indexador ou no servidor MCP deve vir acompanhada de testes unitários/integração.

## Arquitetura

Veja [CLAUDE.md](CLAUDE.md) para detalhes de arquitetura, módulos internos (`chunker.py`, `embeddings.py`, `storage.py`, `indexer.py`, `server.py`, `models.py`) e convenções de código, e [.memory-bank/constitution.md](.memory-bank/constitution.md) para os princípios que regem o projeto.

## Pipeline de indexação (detalhado)

O núcleo da indexação é `index_workspace()` (`src/codesteer_atlas/indexer.py`), compartilhado pelo CLI (`atlas-index`) e pela tool MCP `atlas_index`. O pipeline roda 100% local — nenhum código-fonte é enviado para serviços externos.

### Fluxo

```
Varredura → Filtros → Hash SHA-256 → Chunking (AST) → Embeddings → LanceDB + manifest.json
```

1. **Varredura** — percorre o workspace (ou as subpastas informadas em `--paths`) de forma recursiva.
2. **Filtros** — ignora pastas como `.git`, `node_modules`, `.venv`, `__pycache__` e `.code-index`; arquivos ocultos; extensões não suportadas; e arquivos acima de 2 MB.
3. **Incremental** — calcula o hash SHA-256 de cada arquivo elegível e compara com o `manifest.json`. Arquivos inalterados são pulados; novos, alterados ou deletados são processados. `--full` ignora os hashes e força reindexação completa.
4. **Chunking** — o `ASTChunker` divide cada arquivo em `CodeChunk`s:
   - **Código com AST** (Python, JS/TS, Go, Java, C#, etc.): parse via Tree-sitter; extrai classes, funções e métodos com nome hierárquico (ex.: `UserService.authenticate`). Se nenhum símbolo for encontrado, gera um chunk `module` com o arquivo inteiro.
   - **SQL** (`.sql`): divide por statement (`CREATE TABLE`, `CREATE VIEW`, `SELECT`, etc.) via Tree-sitter; nomeia chunks pela tabela/view/função ou `select_<tabela>`; statements grandes são particionados por linhas (~1000 caracteres).
   - **Markdown** (`.md`): divide por cabeçalhos (`#`, `##`, …); seções grandes são quebradas por parágrafos.
   - **Texto / sem parser** (`.txt`, `.xml`, `.razor`, etc.): agrupa parágrafos em blocos de até ~1000 caracteres.
   - Chunks muito grandes são truncados preservando assinatura/docstring (primeiras linhas) e retorno (últimas linhas).
5. **Embeddings** — apenas chunks novos ou alterados passam pelo `EmbeddingEngine` (`fastembed`, modelo `all-MiniLM-L6-v2`, 384 dimensões, processamento em lote).
6. **Persistência** — grava em `.code-index/`:
   - `lancedb/` — tabela `chunks` com vetores e índice FTS (BM25) na coluna `content`.
   - `manifest.json` — metadados (total de chunks, linguagens, modelo, `git_head_sha`, versão do índice) e mapa `arquivo → hash` para indexação incremental.

Na primeira execução (ou com `--full` sem `--paths`), o índice é sobrescrito por completo. Nas demais, chunks de arquivos alterados ou removidos são deletados e os novos são inseridos, preservando o restante do índice.

## Resolução do diretório de índice (detalhado)

O diretório `.code-index` é resolvido nesta ordem (`resolve_index_dir()` em `server.py`):

1. Argumento `--index-dir` da CLI/servidor.
2. Variável de ambiente `ATLAS_INDEX_DIR`.
3. Busca ascendente a partir do diretório atual por uma pasta `.code-index` (estilo `.git`).
4. Padrão `.code-index` relativo ao diretório atual (`DEFAULT_INDEX_DIR`).

## Configuração manual em outros clientes

Há dois modos suportados para registrar o servidor MCP — escolha um e use-o de forma consistente em todos os clientes. Em ambos, substitua `/caminho/para/.code-index` pelo diretório do índice do workspace alvo (gerado por `atlas-index --workspace .`, veja [README](README.md#início-rápido-primeira-vez)).

- **Remoto** (`uvx`) — não requer instalação nem clonar o repositório; baixa o pacote do GitHub a cada execução.
- **Instalado** (`uv tool install`) — instala `atlas-serve`/`atlas-index` uma vez no PATH (`uv tool install git+https://github.com/LuisCarlosLopes/codesteer-atlas.git`); execuções subsequentes são instantâneas.

**Modo remoto:**

```json
{
  "command": "uvx",
  "args": [
    "--from", "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
    "atlas-serve",
    "--index-dir", "/caminho/para/.code-index"
  ],
  "env": {
    "ATLAS_INDEX_DIR": "/caminho/para/.code-index"
  }
}
```

**Modo instalado:**

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "atlas-serve",
      "env": {
        "ATLAS_INDEX_DIR": "/caminho/para/.code-index"
      }
    }
  }
}
```

Os exemplos por cliente abaixo usam o modo remoto; para usar o modo instalado, basta substituir `command`/`args` pelo bloco "Modo instalado" acima.

### Claude Code (CLI)

```bash
# Modo remoto
claude mcp add codesteer-atlas -- uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-serve --index-dir /caminho/para/.code-index

# Modo instalado
claude mcp add codesteer-atlas -- atlas-serve --index-dir /caminho/para/.code-index
```

Ou adicione manualmente em `.mcp.json` (na raiz do projeto) ou na config global do Claude Code, usando um dos blocos JSON acima dentro de `mcpServers.codesteer-atlas`.

### Cursor

Este repositório já inclui um [`.cursor/mcp.json`](.cursor/mcp.json) pronto, em modo remoto (`uvx`, sem paths absolutos). Copie esse arquivo para a raiz do seu projeto (ou para `~/.cursor/mcp.json` para configuração global) e reinicie o Cursor.

Para usar o modo instalado em vez do remoto, edite o arquivo copiado com o bloco "Modo instalado" acima.

### OpenCode

Crie/edite `opencode.json` na raiz do projeto (ou `~/.config/opencode/opencode.json` para configuração global):

```json
{
  "mcp": {
    "codesteer-atlas": {
      "type": "local",
      "command": [
        "uvx", "--from", "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
        "atlas-serve",
        "--index-dir", "/caminho/para/.code-index"
      ],
      "environment": {
        "ATLAS_INDEX_DIR": "/caminho/para/.code-index"
      },
      "enabled": true
    }
  }
}
```

`"type": "local"` aqui se refere ao transporte stdio (vs. servidor remoto via URL), não ao modo de execução do Atlas — funciona tanto com `uvx` (remoto) quanto com `atlas-serve` (instalado).

### Kiro

Este repositório já inclui um [`.kiro/settings/mcp.json`](.kiro/settings/mcp.json) pronto, em modo remoto (`uvx`, sem paths absolutos), com `autoApprove` para as tools somente-leitura (`atlas_search`, `atlas_map`, `atlas_status`). Copie esse arquivo para a raiz do seu projeto (ou para a configuração global do Kiro) e reinicie.

Para usar o modo instalado em vez do remoto, edite o arquivo copiado com o bloco "Modo instalado" acima.

#### Power do Kiro

Este repositório também é distribuído como um [Power do Kiro](https://kiro.dev/docs/powers/create/) ([`POWER.md`](POWER.md) + [`mcp.json`](mcp.json)), que já inclui o passo de onboarding (verificação do `uv`/`uvx` e indexação inicial do workspace) e orientações de uso das tools `atlas_*`. Para instalar:

1. No Kiro, vá em **Add Custom Power → Import power from GitHub**.
2. Informe o repositório `https://github.com/LuisCarlosLopes/codesteer-atlas.git`.
3. Siga o onboarding do power para indexar o workspace atual.

### GitHub Copilot (VS Code)

Este repositório já inclui um [`.vscode/mcp.json`](.vscode/mcp.json) pronto, em modo remoto (`uvx`, sem paths absolutos). Copie esse arquivo para a raiz do seu projeto.

Para usar o modo instalado em vez do remoto, edite o arquivo copiado com o bloco "Modo instalado" acima (note que o VS Code usa a chave `servers`, e não `mcpServers`).

Após salvar, abra o painel de MCP Servers do Copilot Chat (Command Palette → "MCP: List Servers") e inicie o `codesteer-atlas`.

#### Plugin do GitHub Copilot CLI

Este repositório também é um [plugin do Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-creating) ([`plugin.json`](plugin.json) + [`.mcp.json`](.mcp.json)), em modo remoto (`uvx`, sem paths absolutos). Para instalar:

```bash
copilot plugin install LuisCarlosLopes/codesteer-atlas

# ou, a partir de uma pasta local clonada
copilot plugin install /caminho/para/codesteer-atlas
```

Para remover: `copilot plugin uninstall codesteer-atlas`.
