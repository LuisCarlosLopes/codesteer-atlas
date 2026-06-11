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
- [uv](https://github.com/astral-sh/uv) (gerenciador de pacotes/ambientes) — fornece o `uvx`, usado para rodar o Atlas sem clonar o repositório.

## Instalação

Para usar o Atlas em um projeto você **não precisa clonar este repositório**: tudo roda via `uvx`, direto do GitHub. A instalação tem dois passos — registrar o servidor MCP no seu cliente (abaixo) e indexar o seu projeto (veja [Início rápido](#início-rápido-primeira-vez)).

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

### Outros clientes (Cursor, Claude Desktop, Cline, Copilot, Kiro, OpenCode...)

- **Manifests prontos**: copie o arquivo de configuração do cliente (ex.: [`.cursor/mcp.json`](.cursor/mcp.json), [`.kiro/settings/mcp.json`](.kiro/settings/mcp.json), [`.vscode/mcp.json`](.vscode/mcp.json)) para a raiz do seu projeto — veja [Configuração manual em outros clientes](#configuração-manual-em-outros-clientes).
- **Instalador interativo** (a partir de um clone deste repositório):

  ```bash
  uv run python deploy_mcp.py
  ```

  Detecta clientes MCP disponíveis (Cursor, Claude Desktop, Cline, Claude Code CLI) e registra o servidor neles, em modo local (este repositório) ou remoto (via `uvx` direto do GitHub).
- **Configuração manual**: veja os blocos JSON em [Configuração manual em outros clientes](#configuração-manual-em-outros-clientes).

> **Vai contribuir com o desenvolvimento do Atlas?** Aí sim você precisa clonar o repositório e instalar as dependências de desenvolvimento — veja [Desenvolvimento](#desenvolvimento).

## Início rápido (primeira vez)

O Atlas indexa **o repositório do seu projeto** — o código que você quer pesquisar — e grava o índice em `.code-index/` na **raiz desse projeto**. Você não indexa o repositório `codesteer-atlas` a menos que esse seja o projeto em que você está trabalhando.

### 1. Entre na raiz do seu projeto

```bash
cd /caminho/para/seu-projeto
```

### 2. Rode a indexação inicial

Na primeira execução, todos os arquivos elegíveis são processados e o índice é criado do zero.

**Opção recomendada** — sem clonar o Atlas (só precisa do `uv`/`uvx`):

```bash
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .
```

**Opção instalar via `uv`** — instala `atlas-index` e `atlas-serve` como ferramentas globais (sem clonar e sem baixar o pacote a cada execução):

```bash
uv tool install git+https://github.com/LuisCarlosLopes/codesteer-atlas.git

# uso
atlas-index --workspace .

# atualizar para a versão mais recente
uv tool upgrade codesteer-atlas
```

**Opção local** — clone este repositório e instale as dependências:

```bash
git clone https://github.com/LuisCarlosLopes/codesteer-atlas.git
cd codesteer-atlas

# macOS / Linux
./setup.sh

# Windows (PowerShell)
.\setup.ps1
```

Depois, rode a indexação apontando para o seu projeto:

```bash
uv --directory /caminho/para/codesteer-atlas run atlas-index --workspace /caminho/para/seu-projeto
# ou, estando dentro do clone do Atlas com o projeto como alvo:
uv run atlas-index --workspace /caminho/para/seu-projeto
```

Ao terminar, você deve ver `Indexação Concluída com Sucesso!` e a pasta `.code-index/` na raiz do **seu projeto**, contendo `manifest.json` e `lancedb/`.

> **Git:** adicione `.code-index/` ao `.gitignore` do seu projeto se ainda não estiver lá — o índice é artefato local, não deve ir para o repositório.

### 3. Conecte um cliente MCP

Se ainda não tiver feito isso, configure o cliente (Cursor, Claude Code, Cline, etc.) para usar o servidor `codesteer-atlas` — veja [Instalação](#instalação). A maioria dos exemplos deste README usa modo remoto (`uvx`) e **descobre automaticamente** a pasta `.code-index` na raiz do projeto aberto — não é necessário passar `--index-dir` manualmente.

### 4. Reindexar depois

Nas execuções seguintes, rode o **mesmo comando** do passo 2. A indexação é **incremental**: só arquivos novos ou alterados são reprocessados.

```bash
# Reindexação incremental (padrão)
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .

# Forçar reconstrução completa do índice
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace . --full

# Indexar apenas partes do projeto
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace . --paths src --paths docs
```

Também é possível reindexar pelo cliente MCP com a tool `atlas_index` (útil após mudanças grandes no código).

## Uso

Com o cliente MCP conectado (veja [Instalação](#instalação)) e o projeto indexado (veja [Início rápido](#início-rápido-primeira-vez)), o Atlas expõe as seguintes tools ao agente:

| Tool | Descrição |
|---|---|
| `atlas_search` | Busca híbrida (vetorial + BM25 + RRF) por trechos de código relevantes. Suporta filtros por `repo`, `language` e `path_prefix`. |
| `atlas_map` | Mapa hierárquico de classes/funções/métodos do workspace indexado. |
| `atlas_index` | Indexa/reindexa o workspace. Suporta `dry_run` para listar candidatos antes de indexar. |
| `atlas_status` | Status e metadados de diagnóstico do índice (existência, total de chunks, modelo, staleness etc.). |

Também expõe o recurso somente leitura `atlas://status`.

Para reindexar após mudanças no código, rode novamente o comando de [indexação](#4-reindexar-depois) (incremental por padrão) ou peça ao agente para usar a tool `atlas_index`.

## Como o código é indexado

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

### Exemplo de chunks gerados

Para um arquivo Python típico, em vez de indexar o arquivo inteiro de uma vez, o Atlas cria um chunk por símbolo:

```
src/auth/service.py
  ├── class AuthService          (linhas 10–45)
  ├── AuthService.login          (linhas 20–35)
  └── AuthService.logout         (linhas 37–44)
```

Cada chunk vira um registro pesquisável por similaridade vetorial (cosseno) e BM25, fundidos via RRF na busca (`atlas_search`).

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

**Modo instalado** (após `uv tool install git+https://github.com/LuisCarlosLopes/codesteer-atlas.git`, o comando `atlas-serve` fica disponível direto no PATH):

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

Os exemplos por cliente abaixo usam o modo local; para usar o modo remoto ou instalado, basta substituir o bloco `command`/`args` pelo equivalente acima.

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

#### Power do Kiro

Este repositório também é distribuído como um [Power do Kiro](https://kiro.dev/docs/powers/create/) ([`POWER.md`](POWER.md) + [`mcp.json`](mcp.json)), que já inclui o passo de onboarding (verificação do `uv`/`uvx` e indexação inicial do workspace) e orientações de uso das tools `atlas_*`. Para instalar:

1. No Kiro, vá em **Add Custom Power → Import power from GitHub**.
2. Informe o repositório `LuisCarlosLopes/codesteer-atlas`.
3. Siga o onboarding do power para indexar o workspace atual.

### GitHub Copilot (VS Code)

Este repositório já inclui um [`.vscode/mcp.json`](.vscode/mcp.json) pronto, em modo remoto (`uvx`, sem paths absolutos). Copie esse arquivo para a raiz do seu projeto.

Para usar o modo local em vez do remoto, edite o arquivo copiado com o formato `servers` (note que o VS Code usa a chave `servers`, e não `mcpServers`).

Após salvar, abra o painel de MCP Servers do Copilot Chat (Command Palette → "MCP: List Servers") e inicie o `codesteer-atlas`.

> **Antes do primeiro uso:** indexe a raiz do **seu projeto** com `atlas-index --workspace .` (veja [Início rápido](#início-rápido-primeira-vez)). Só rode `./setup.sh`/`.\setup.ps1` se estiver usando o Atlas em modo local a partir deste repositório clonado.

#### Plugin do GitHub Copilot CLI

Este repositório também é um [plugin do Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-creating) ([`plugin.json`](plugin.json) + [`.mcp.json`](.mcp.json)), em modo remoto (`uvx`, sem paths absolutos). Para instalar:

```bash
copilot plugin install LuisCarlosLopes/codesteer-atlas

# ou, a partir de uma pasta local clonada
copilot plugin install /caminho/para/codesteer-atlas
```

Para remover: `copilot plugin uninstall codesteer-atlas`.

> **Antes do primeiro uso:** indexe a raiz do **seu projeto** com `atlas-index --workspace .` (veja [Início rápido](#início-rápido-primeira-vez)).

## Resolução do diretório de índice

O diretório `.code-index` é resolvido nesta ordem:

1. Argumento `--index-dir` da CLI/servidor.
2. Variável de ambiente `ATLAS_INDEX_DIR`.
3. Busca ascendente a partir do diretório atual por uma pasta `.code-index` (estilo `.git`).
4. Padrão `.code-index` relativo ao diretório atual.

## Desenvolvimento

Para desenvolver o Atlas, clone este repositório e instale as dependências:

```bash
# macOS / Linux
./setup.sh

# Windows (PowerShell)
.\setup.ps1
```

O script verifica o `uv`, sincroniza as dependências (`uv sync --group dev`) e valida os imports críticos via `deploy_mcp.py --check`.

### Rodar o servidor MCP localmente

```bash
uv run atlas-serve

# Apontando para um diretório de índice específico
uv run atlas-serve --index-dir /caminho/para/.code-index
```

O servidor se comunica via stdio (JSON-RPC), pronto para ser usado por clientes MCP (Claude Code, Claude Desktop, Cursor, Cline, etc.).

### Testes e lint

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
