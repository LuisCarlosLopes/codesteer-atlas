# CodeSteer Atlas

Servidor MCP (Model Context Protocol) local para busca semântica em bases de código, usando Tree-sitter para parsing AST, embeddings locais via `fastembed` (ONNX) e LanceDB como banco vetorial embutido.

Tudo roda 100% local e offline — o código-fonte nunca é enviado para serviços externos.

## Funcionalidades

- **Indexação por AST (Tree-sitter)**: extrai classes, funções e métodos como chunks de contexto coerentes, em vez de blocos arbitrários de linhas.
- **Busca híbrida**: combina similaridade vetorial (cosseno) com busca lexical BM25 (full-text), fundidas via Reciprocal Rank Fusion (RRF).
- **Indexação incremental**: reindexações subsequentes processam apenas arquivos novos/alterados (hash sha256), tornando re-execuções rápidas.
- **Embeddings locais**: modelo `all-MiniLM-L6-v2` (384 dimensões) via `fastembed`, com lazy loading.
- **Mapa de arquitetura**: visão hierárquica de classes/funções/métodos do workspace, sem precisar carregar arquivos inteiros.
- **Multi-linguagem**: Python, JavaScript, TypeScript/TSX, Go, Java, C#, Dart, Pascal, VB6, Razor, XML, Markdown e mais.

## Pré-requisitos

- Python 3.11 a 3.13
- [uv](https://github.com/astral-sh/uv) (gerenciador de pacotes/ambientes)

## Instalação

```bash
# macOS / Linux
./setup.sh

# Windows (PowerShell)
.\setup.ps1
```

O script verifica o `uv`, sincroniza as dependências (`uv sync --group dev`) e valida os imports críticos via `deploy_mcp.py --check`.

## Uso

### 1. Indexar um workspace

```bash
# Indexação completa/incremental do diretório atual
uv run atlas-index --workspace .

# Forçar reindexação completa
uv run atlas-index --workspace . --full

# Indexar apenas subpastas específicas
uv run atlas-index --workspace . --paths src --paths docs
```

A indexação é incremental por padrão: arquivos cujo conteúdo não mudou são pulados nas execuções seguintes.

### 2. Iniciar o servidor MCP

```bash
uv run atlas-serve

# Apontando para um diretório de índice específico
uv run atlas-serve --index-dir /caminho/para/.code-index
```

O servidor se comunica via stdio (JSON-RPC), pronto para ser usado por clientes MCP (Claude Code, Claude Desktop, Cursor, Cline, etc.).

### 3. Conectar a um cliente MCP

```bash
uv run python deploy_mcp.py
```

Instalador interativo que detecta clientes MCP disponíveis (Cursor, Claude Desktop, Cline, Claude Code CLI) e registra o servidor neles, em modo local (este repositório) ou remoto (via `uvx` direto do GitHub).

### Plugin do Claude Code

Este repositório também é distribuído como plugin/marketplace do Claude Code (`.claude-plugin/`). Não é necessário publicar em nenhum marketplace público — o marketplace pode ser adicionado a partir do próprio repositório git (privado ou público) ou de uma pasta local:

```
# A partir do repositório git (privado ou público)
/plugin marketplace add LuisCarlosLopes/codesteer-atlas

# Ou a partir de uma pasta local clonada
/plugin marketplace add /caminho/para/codesteer-atlas

# Em ambos os casos
/plugin install codesteer-atlas
```

O plugin registra o `codesteer-atlas` em modo remoto (`uvx`), sem caminhos absolutos no manifest. Não é necessário configurar `--index-dir`/`ATLAS_INDEX_DIR`: o servidor descobre automaticamente a pasta `.code-index` na raiz do seu projeto (busca ascendente a partir do CWD). Basta indexar o workspace uma vez:

```bash
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .
```

## Configuração manual em outros clientes

O `deploy_mcp.py` automatiza Cursor, Claude Desktop, Cline e Claude Code CLI, em dois modos:

- **Local**: aponta para a pasta deste repositório clonado na máquina (`uv --directory ...`).
- **Remoto**: roda direto do GitHub via `uvx`, sem precisar clonar o repositório.

Para outros clientes (ou configuração manual), use um dos blocos abaixo, substituindo `/caminho/para/.code-index` pelo diretório do índice do workspace alvo (gerado por `uv run atlas-index --workspace .`).

**Modo local** (substitua também `/caminho/para/codesteer-atlas` pelo diretório deste repositório):

```json
{
  "command": "uv",
  "args": [
    "--directory", "/caminho/para/codesteer-atlas",
    "run", "atlas-serve",
    "--index-dir", "/caminho/para/.code-index"
  ],
  "env": {
    "ATLAS_INDEX_DIR": "/caminho/para/.code-index"
  }
}
```

**Modo remoto** (não requer clonar o repositório, requer apenas `uvx`):

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

Os exemplos por cliente abaixo usam o modo local; para usar o modo remoto, basta substituir o bloco `command`/`args` pelo equivalente acima.

### Claude Code (CLI)

```bash
# Modo local
claude mcp add codesteer-atlas -- uv --directory /caminho/para/codesteer-atlas run atlas-serve --index-dir /caminho/para/.code-index

# Modo remoto (sem clonar o repo)
claude mcp add codesteer-atlas -- uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-serve --index-dir /caminho/para/.code-index
```

Ou adicione manualmente em `.mcp.json` (na raiz do projeto) ou na config global do Claude Code:

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "uv",
      "args": [
        "--directory", "/caminho/para/codesteer-atlas",
        "run", "atlas-serve",
        "--index-dir", "/caminho/para/.code-index"
      ],
      "env": {
        "ATLAS_INDEX_DIR": "/caminho/para/.code-index"
      }
    }
  }
}
```


### Cursor

Este repositório já inclui um [`.cursor/mcp.json`](.cursor/mcp.json) pronto, em modo remoto (`uvx`, sem paths absolutos). Copie esse arquivo para a raiz do seu projeto (ou para `~/.cursor/mcp.json` para configuração global) e reinicie o Cursor.

Para usar o modo local em vez do remoto, edite o arquivo copiado com o formato `mcpServers` da seção anterior.

### OpenCode

Crie/edite `opencode.json` na raiz do projeto (ou `~/.config/opencode/opencode.json` para configuração global):

```json
{
  "mcp": {
    "codesteer-atlas": {
      "type": "local",
      "command": [
        "uv", "--directory", "/caminho/para/codesteer-atlas",
        "run", "atlas-serve",
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

### Kiro

Este repositório já inclui um [`.kiro/settings/mcp.json`](.kiro/settings/mcp.json) pronto, em modo remoto (`uvx`, sem paths absolutos), com `autoApprove` para as tools somente-leitura (`atlas_search`, `atlas_map`, `atlas_status`). Copie esse arquivo para a raiz do seu projeto (ou para a configuração global do Kiro) e reinicie.

Para usar o modo local em vez do remoto, edite o arquivo copiado com o formato `mcpServers` da seção anterior.

### GitHub Copilot (VS Code)

Este repositório já inclui um [`.vscode/mcp.json`](.vscode/mcp.json) pronto, em modo remoto (`uvx`, sem paths absolutos). Copie esse arquivo para a raiz do seu projeto.

Para usar o modo local em vez do remoto, edite o arquivo copiado com o formato `servers` (note que o VS Code usa a chave `servers`, e não `mcpServers`).

Após salvar, abra o painel de MCP Servers do Copilot Chat (Command Palette → "MCP: List Servers") e inicie o `codesteer-atlas`.

> Em todos os clientes, lembre-se de rodar `./setup.sh`/`.\setup.ps1` (instala as dependências) e `uv run atlas-index --workspace .` (gera o índice) antes do primeiro uso.

## Resolução do diretório de índice

O diretório `.code-index` é resolvido nesta ordem:

1. Argumento `--index-dir` da CLI/servidor.
2. Variável de ambiente `ATLAS_INDEX_DIR`.
3. Busca ascendente a partir do diretório atual por uma pasta `.code-index` (estilo `.git`).
4. Padrão `.code-index` relativo ao diretório atual.

## Ferramentas MCP disponíveis

| Tool | Descrição |
|---|---|
| `atlas_search` | Busca híbrida (vetorial + BM25 + RRF) por trechos de código relevantes. Suporta filtros por `repo`, `language` e `path_prefix`. |
| `atlas_map` | Mapa hierárquico de classes/funções/métodos do workspace indexado. |
| `atlas_index` | Indexa/reindexa o workspace. Suporta `dry_run` para listar candidatos antes de indexar. |
| `atlas_status` | Status e metadados de diagnóstico do índice (existência, total de chunks, modelo, staleness etc.). |

Também expõe o recurso somente leitura `atlas://status`.

## Desenvolvimento

```bash
# Rodar testes
uv run pytest -v

# Rodar lint
uv run ruff check
```

## Arquitetura

Veja [CLAUDE.md](CLAUDE.md) para detalhes de arquitetura, módulos internos e convenções de código, e [.memory-bank/constitution.md](.memory-bank/constitution.md) para os princípios que regem o projeto.

## Licença

Veja [LICENSE](LICENSE).
