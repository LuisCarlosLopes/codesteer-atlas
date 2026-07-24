# CodeSteer Atlas

Servidor MCP (Model Context Protocol) local para busca semântica em bases de código, usando Tree-sitter para parsing AST, embeddings locais via `fastembed` (ONNX) e LanceDB como banco vetorial embutido.

Tudo roda 100% local e offline — o código-fonte nunca é enviado para serviços externos.

### Documentação

| Recurso | Descrição |
| -------- | ---------- |
| 📖 [Documentação visual](https://luiscarloslopes.github.io/codesteer-atlas/) | Conceitos MCP, busca híbrida e indexação (site interativo) |
| 📘 [Guia didático — Indexação, Grafo e MCP](docs/guia-indexacao-grafo-mcp.md) | Pipeline completo, diagramas, multi-repo, `atlas_graph` e `graph.html` |

## Funcionalidades

- **Indexação por AST (Tree-sitter)**: extrai classes, funções e métodos como chunks de contexto coerentes, em vez de blocos arbitrários de linhas.
- **Busca híbrida**: combina similaridade vetorial (cosseno) com busca lexical BM25 (full-text), fundidas via Reciprocal Rank Fusion (RRF).
- **Indexação incremental**: reindexações subsequentes processam apenas arquivos novos/alterados (hash sha256), tornando re-execuções rápidas.
- **Embeddings locais**: modelo `all-MiniLM-L6-v2` (384 dimensões) via `fastembed`, com lazy loading.
- **Mapa de arquitetura**: visão hierárquica de classes/funções/métodos do workspace, sem precisar carregar arquivos inteiros.
- **Rationale refs em código**: comentários `NOTE` / `WHY`, cites `DEC/ADR/RFC` e wikilinks viram metadados persistidos no índice e podem aparecer em `atlas_search`.
- **Grafo de conhecimento derivado**: gera `.code-index/graph.json` conectando arquivos, símbolos, markdown, imports e rationale para consultas de conectividade.
- **Visualizador local do grafo**: gera `.code-index/graph.html` autocontido, abrível via `file://`, com pan/zoom, filtros, busca e painel de detalhes.
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
#### Power do Kiro

Este repositório também é distribuído como um [Power do Kiro](https://kiro.dev/docs/powers/create/) ([`POWER.md`](POWER.md) + [`mcp.json`](mcp.json)), que já inclui o passo de onboarding (verificação do `uv`/`uvx` e indexação inicial do workspace) e orientações de uso das tools `atlas_*`. Para instalar:

1. No Kiro, vá em **Add Custom Power → Import power from GitHub**.
2. Informe o repositório `https://github.com/LuisCarlosLopes/codesteer-atlas.git`.
3. Siga o onboarding do power para indexar o workspace atual.

#### Plugin do GitHub Copilot CLI

Este repositório também é um [plugin do Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-creating) ([`plugin.json`](plugin.json) + [`.mcp.json`](.mcp.json)), em modo remoto (`uvx`, sem paths absolutos). Para instalar:

```bash
copilot plugin install LuisCarlosLopes/codesteer-atlas

# ou, a partir de uma pasta local clonada
copilot plugin install /caminho/para/codesteer-atlas
```

Para remover: `copilot plugin uninstall codesteer-atlas`.

### Outros clientes (Cursor, Cline, Copilot, Kiro, OpenCode...)

Copie o manifest pronto do cliente correspondente para a raiz do seu projeto (ou para a configuração global dele) e reinicie o cliente:

- Cursor: [`.cursor/mcp.json`](.cursor/mcp.json)
- Kiro: [`.kiro/settings/mcp.json`](.kiro/settings/mcp.json)
- OpenCode: [`.opencode/opencode.json`](.opencode/opencode.json)
- GitHub Copilot (VS Code): [`.vscode/mcp.json`](.vscode/mcp.json)

Para configuração manual de outros clientes (OpenCode, Claude Desktop, Cline) ou para usar o modo instalado, veja [CONTRIBUTING.md](CONTRIBUTING.md#configuração-manual-em-outros-clientes).

### Modo instalado (opcional)

Se você instalou o Atlas como ferramenta global (`uv tool install git+https://github.com/LuisCarlosLopes/codesteer-atlas.git` — veja [Início rápido](#2-rode-a-indexação-inicial)), o comando `atlas-serve` fica disponível direto no PATH e você pode registrá-lo sem `uvx`:

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

Assim como no modo remoto, `--index-dir`/`ATLAS_INDEX_DIR` são opcionais — o servidor descobre automaticamente a pasta `.code-index` na raiz do projeto aberto.

> **Vai contribuir com o desenvolvimento do Atlas?** Você precisa clonar o repositório e instalar as dependências de desenvolvimento — veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Início rápido (primeira vez)

O Atlas indexa **o repositório do seu projeto** — o código que você quer pesquisar — e grava o índice em `.code-index/` na **raiz desse projeto**. Você não indexa o repositório `codesteer-atlas` a menos que esse seja o projeto em que você está trabalhando.

### 1. Entre na raiz do seu projeto

```bash
cd /caminho/para/seu-projeto
```

### 2. Rode a indexação inicial

Na primeira execução, todos os arquivos elegíveis são processados e o índice é criado do zero. Não é necessário clonar o Atlas.

**Recomendado** — instale `atlas-index`/`atlas-serve` como ferramentas globais (uma vez só):

```bash
uv tool install git+https://github.com/LuisCarlosLopes/codesteer-atlas.git
```

E rode:

```bash
atlas-index --workspace .
```

Para atualizar para a versão mais recente: `uv tool upgrade codesteer-atlas`.

> Alternativa sem instalar nada: `uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .` (baixa o pacote a cada execução).

Ao terminar, você deve ver `Indexação Concluída com Sucesso!` e a pasta `.code-index/` na raiz do **seu projeto**, contendo `manifest.json`, `lancedb/`, `graph.json` e `graph.html`.

> **Git:** adicione `.code-index/` ao `.gitignore` do seu projeto se ainda não estiver lá — o índice é artefato local, não deve ir para o repositório.

### 3. Conecte um cliente MCP

Se ainda não tiver feito isso, configure o cliente (Cursor, Claude Code, Cline, etc.) para usar o servidor `codesteer-atlas` — veja [Instalação](#instalação). A maioria dos exemplos deste README usa modo remoto (`uvx`) e **descobre automaticamente** a pasta `.code-index` na raiz do projeto aberto — não é necessário passar `--index-dir` manualmente.

### 4. Reindexar depois

Nas execuções seguintes, rode o **mesmo comando** do passo 2. A indexação é **incremental**: só arquivos novos ou alterados são reprocessados.

```bash
# Reindexação incremental (padrão)
atlas-index --workspace .

# Forçar reconstrução completa do índice
atlas-index --workspace . --full

# Indexar apenas partes do projeto
atlas-index --workspace . --paths src --paths docs
```

Também é possível reindexar pelo cliente MCP com a tool `atlas_index` (útil após mudanças grandes no código).

> **Reindex automático no startup:** sempre que o servidor MCP (`atlas-serve`) inicia — ou seja, sempre que você abre/reinicia seu editor/cliente MCP — ele dispara automaticamente uma reindexação incremental em background (sem bloquear o cliente). Isso só acontece se já existir um índice em `.code-index/`; a primeira indexação continua precisando ser feita manualmente (passo 2). O log fica em `.code-index/background_reindex.log`.

## Uso

Com o cliente MCP conectado (veja [Instalação](#instalação)) e o projeto indexado (veja [Início rápido](#início-rápido-primeira-vez)), o Atlas expõe as seguintes tools ao agente:

| Tool | Descrição |
|---|---|
| `atlas_search` | Busca híbrida (vetorial + BM25 + RRF). Por padrão retorna só metadados (`file_path`, linhas, símbolo, score); use `include_content=true` ou `Read` nas linhas indicadas para o conteúdo. Filtros: `repo`, `language`, `path_prefix`. Resultados de código podem incluir `rationale_refs`. |
| `atlas_map` | Mapa hierárquico de classes/funções/métodos do workspace indexado. |
| `atlas_graph` | Consulta o grafo derivado do índice: `hubs`, `path` e `explain`, conectando código, markdown e rationale. |
| `atlas_index` | Indexa/reindexa o workspace. Suporta `dry_run` para listar candidatos antes de indexar. Também regenera `.code-index/graph.json` e `.code-index/graph.html`. |
| `atlas_status` | Status e metadados de diagnóstico do índice (existência, total de chunks, modelo, staleness, `graph_available`, `graph_viewer_path`). |

Também expõe o recurso somente leitura `atlas://status`.

Para reindexar após mudanças no código, rode novamente o comando de [indexação](#4-reindexar-depois) (incremental por padrão) ou peça ao agente para usar a tool `atlas_index`.

> **Nota de upgrade:** `atlas_graph`, `graph.json` e `graph.html` exigem um reindex em índices antigos (< `2.1.0`). Índices `2.0.0` continuam respondendo busca e mapa normalmente.

Após a indexação, o Atlas também grava `.code-index/graph.html`, um visualizador autocontido do grafo. Você pode abri-lo com duplo-clique (`file://`) para inspecionar hubs, caminhos e clusters sem rodar servidor web.

### Novos recursos de conhecimento

**`atlas_search` com rationale refs**

Em resultados de código, `atlas_search` pode incluir `rationale_refs` quando o chunk contém referências estruturadas como:

- `DECISAO-002`, `DEC-002`, `ADR-001`, `RFC-012`
- `[[wikilinks]]` para notas markdown
- comentários `# NOTE:` e `# WHY:` (ou `//`, `--`, `*`)

Isso permite ao agente responder não só "onde está", mas também "qual decisão embasa este trecho".

**`atlas_graph`**

Use a tool `atlas_graph` quando a pergunta for sobre conectividade:

- `mode="hubs"`: encontra os nós mais conectados do workspace
- `mode="path"`: encontra caminho entre dois arquivos, símbolos ou notas
- `mode="explain"`: resume a vizinhança de um nó, incluindo rationale e notas ligadas

Exemplos:

```text
atlas_graph(mode="hubs", top_n=10)
atlas_graph(mode="path", source="src/app.py", target="dec-002")
atlas_graph(mode="explain", target="AuthService.login")
```

**`atlas_status` com artefatos de grafo**

`atlas_status` agora também informa:

- `graph_available`: se o índice atual já possui `graph.json`
- `graph_viewer_path`: path absoluto do `graph.html`, quando disponível

Isso permite ao agente orientar o usuário a abrir o visualizador local diretamente.

## Instruções para agentes de IA (AGENTS.md / CLAUDE.md)

Para que agentes de codificação usem o Atlas de forma consistente, copie o bloco abaixo para o arquivo de instruções do seu projeto ou cliente:

| Cliente / IDE | Arquivo equivalente |
|---|---|
| Cursor, Copilot (VS Code), genérico | [`AGENTS.md`](AGENTS.md) |
| Claude Code | [`CLAUDE.md`](CLAUDE.md) |
| Kiro | regras do Power / instruções do agente |
| GitHub Copilot CLI | instruções do plugin ou regras do projeto |

```markdown
# Busca de código com `codesteer-atlas`

Este repositório é indexado pelo MCP `codesteer-atlas`. Para Entender como algo funciona, Pesquisar, localizar ou explorar, use Atlas antes de `grep`, `rg`, `find`, glob ou leitura em massa.

## Use assim

- `atlas_search`: localizar função, classe, método, símbolo ou conceito
- `atlas_map`: entender a estrutura do projeto sem abrir muitos arquivos
- `atlas_graph`: inspecionar hubs, paths e conexões entre código, markdown e rationale
- `atlas_status`: usar só quando houver suspeita de índice ausente ou desatualizado
- `atlas_index`: reindexar após mudanças grandes ou índice stale

## Fluxo padrão

1. Rode `atlas_search` para descoberta.
2. Restrinja com `path_prefix` e `language` quando fizer sentido.
3. Leia apenas os hits relevantes com `Read`, ou repita com `include_content=true`.

`atlas_search` retorna metadados por padrão. Localize primeiro; leia conteúdo depois.

## Quando pode pular o Atlas

- o usuário já informou o caminho exato do arquivo
- você precisa confirmar uma string literal exata
- a tarefa é edição, diff, commit, git, CI, testes ou instalação de dependências
- o MCP estiver indisponível, sem autenticação ou com índice vazio/desatualizado

## Índice desatualizado

1. Rode `atlas_status`.
2. Se necessário, rode `atlas_index`.
3. Use fallback local só se o problema persistir.

Priorize esse fluxo especialmente em etapas de descoberta, especificação e planejamento.
```

## Como funciona

O Atlas divide cada arquivo em `CodeChunk`s no nível de símbolo (classes, funções, métodos) via Tree-sitter, gera embeddings locais para cada chunk e indexa tudo em LanceDB com busca vetorial + BM25 (RRF). Para um arquivo Python típico, em vez de indexar o arquivo inteiro de uma vez, o Atlas cria um chunk por símbolo:

```
src/auth/service.py
  ├── class AuthService          (linhas 10–45)
  ├── AuthService.login          (linhas 20–35)
  └── AuthService.logout         (linhas 37–44)
```

Cada chunk vira um registro pesquisável por similaridade vetorial (cosseno) e BM25, fundidos via RRF na busca (`atlas_search`). Para detalhes do pipeline (filtros, indexação incremental, chunking por linguagem, truncamento, persistência), veja [CONTRIBUTING.md](CONTRIBUTING.md#pipeline-de-indexação-detalhado).

### Excluindo arquivos com `.atlasignore`

Para excluir arquivos/pastas adicionais da indexação sem editar o código do Atlas, crie um arquivo `.atlasignore` na raiz do workspace. A sintaxe é idêntica à do `.gitignore` (glob, `**`, ancoragem com `/`, negação com `!`, comentários `#`):

```gitignore
# Ignora todos os arquivos .log
*.log

# Ignora a pasta inteira (incl. arquivos dentro)
fixtures/

# Ignora apenas na raiz do workspace, não em subpastas
/dist

# Ignora em qualquer profundidade
**/*.generated.py

# Negação: reinclui um arquivo previamente ignorado
!important.log
```

- O arquivo é opcional: workspaces sem `.atlasignore` continuam indexando exatamente como antes.
- `.atlasignore` é um filtro **adicional** — pastas como `.git`, `node_modules`, `.venv`, `__pycache__` e `.code-index` continuam sempre ignoradas e não podem ser "reincluídas" via `!`.
- O filtro é aplicado tanto na indexação real (`atlas-index` / `atlas_index`) quanto no preview (`atlas_index` com `dry_run=true`).

## Resolução do diretório de índice

O diretório `.code-index` é resolvido nesta ordem:

1. Argumento `--index-dir` da CLI/servidor.
2. Variável de ambiente `ATLAS_INDEX_DIR`.
3. Busca ascendente a partir do diretório atual por uma pasta `.code-index` (estilo `.git`).
4. Busca ascendente a partir da raiz do projeto/workspace informada pelo editor: `CLAUDE_PROJECT_DIR` (Claude Code) ou `WORKSPACE_FOLDER_PATHS` (Cursor/VS Code, quando definida).
5. Padrão `.code-index` relativo à raiz do projeto informada pelo editor (se disponível) ou ao diretório atual.

### Instalação via plugin/Power

Os manifests de plugin (Claude Code, Kiro Power, Copilot CLI) não definem `--index-dir`/`ATLAS_INDEX_DIR` — eles dependem dos itens 3 e 4 acima.

Normalmente o item 3 já resolve: busca ascendente a partir do diretório de trabalho do processo do servidor, que costuma ser a raiz do projeto aberto no editor. Basta rodar `atlas-index --workspace .` na raiz do projeto (criando `.code-index/` ali) para que o servidor o encontre automaticamente, sem nenhuma configuração extra.

**Se o servidor MCP do plugin for iniciado com outro CWD** (ex.: o HOME do usuário, em vez da raiz do projeto — o que faria a busca ascendente do item 3 não encontrar o `.code-index` do projeto e, na pior das hipóteses, encontrar ou criar um `.code-index` solto no HOME), o item 4 cobre esse caso: o editor define `CLAUDE_PROJECT_DIR`/`WORKSPACE_FOLDER_PATHS` com a raiz real do projeto, e o servidor refaz a busca ascendente a partir dela. Caso nem assim encontre um `.code-index` existente, o índice padrão (item 5) passa a usar essa raiz — ou seja, mesmo no primeiro `atlas_index` (que cria o diretório), ele é criado na raiz do projeto, não no HOME.

> **Cursor**: roda os servidores MCP em um processo compartilhado entre projetos, com CWD fixo no `$HOME` e **sem** expor `CLAUDE_PROJECT_DIR`/`WORKSPACE_FOLDER_PATHS`. Os itens 3 e 4 não resolvem nesse caso — é necessário definir `ATLAS_INDEX_DIR` por projeto usando `${workspaceFolder}`. Veja [Cursor em CONTRIBUTING.md](CONTRIBUTING.md#cursor).

**Para apontar para um `.code-index` em outro lugar** (ex.: índice compartilhado fora do projeto), defina `ATLAS_INDEX_DIR` — que tem prioridade sobre a busca automática — no manifest MCP:

- **Claude Code**: adicione/edite `.mcp.json` na raiz do projeto (ou a config global) com:

  ```json
  {
    "mcpServers": {
      "codesteer-atlas": {
        "command": "uvx",
        "args": ["--from", "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git", "atlas-serve"],
        "env": {
          "ATLAS_INDEX_DIR": "/caminho/para/.code-index"
        }
      }
    }
  }
  ```

  Uma entrada de `codesteer-atlas` em `.mcp.json` do projeto tem precedência sobre a registrada pelo plugin.

- **Kiro Power / Copilot CLI plugin**: o manifest (`mcp.json`/`.mcp.json`) vem do próprio repositório do Atlas instalado pelo marketplace — não edite-o diretamente (mudanças seriam perdidas em atualizações). Em vez disso, copie o manifest pronto do cliente (ex.: [`.kiro/settings/mcp.json`](.kiro/settings/mcp.json)) para a raiz do seu projeto, adicione `env.ATLAS_INDEX_DIR` e reinicie o cliente — veja [Outros clientes](#outros-clientes-cursor-cline-copilot-kiro-opencode).

## Contribuindo

Quer clonar o repositório, rodar testes, configurar manualmente outros clientes MCP ou entender o pipeline de indexação em detalhes? Veja [CONTRIBUTING.md](CONTRIBUTING.md) e [CLAUDE.md](CLAUDE.md).

## Licença

Veja [LICENSE](LICENSE).
