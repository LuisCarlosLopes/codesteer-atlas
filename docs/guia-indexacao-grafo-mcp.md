# Guia didático — Indexação, Grafo e MCP no CodeSteer Atlas

> **Para quem é este guia:** desenvolvedores e agentes de IA que querem entender
> *como* o Atlas transforma código em índice pesquisável, *como* consultar via MCP
> e *como* usar o grafo de conectividade.
>
> **Pré-requisito:** Python 3.11+ com [uv](https://github.com/astral-sh/uv) e o
> workspace indexado ao menos uma vez.

---

## Sumário

1. [Visão geral em 30 segundos](#1-visão-geral-em-30-segundos)
2. [Arquitetura do sistema](#2-arquitetura-do-sistema)
3. [Pipeline de indexação](#3-pipeline-de-indexação)
4. [Artefatos em `.code-index/`](#4-artefatos-em-code-index)
5. [Indexação incremental](#5-indexação-incremental)
6. [Pastas, subpastas e multi-repo](#6-pastas-subpastas-e-multi-repo)
7. [Ferramentas MCP](#7-ferramentas-mcp)
8. [Busca vs grafo — quando usar cada um](#8-busca-vs-grafo--quando-usar-cada-um)
9. [Grafo de conhecimento](#9-grafo-de-conhecimento)
10. [Visualizador `graph.html`](#10-visualizador-graphhtml)
11. [Enriquecendo o grafo](#11-enriquecendo-o-grafo)
12. [Fluxos práticos passo a passo](#12-fluxos-práticos-passo-a-passo)
13. [Perguntas frequentes](#13-perguntas-frequentes)

---

## 1. Visão geral em 30 segundos

O **CodeSteer Atlas** é um servidor **MCP** (Model Context Protocol) que roda **100%
local**. Ele:

1. **Indexa** seu código e documentos → gera chunks + embeddings + grafo.
2. **Busca** semanticamente (`atlas_search`) combinando vetores e BM25.
3. **Explora conexões** (`atlas_graph`) entre código, docs e decisões arquiteturais.

```mermaid
flowchart LR
    subgraph Entrada
        CODE[Código-fonte]
        DOCS[Markdown / docs]
    end

    subgraph Atlas
        IDX[Indexação]
        LDB[(LanceDB + FTS)]
        GRAFO[graph.json]
    end

    subgraph Saída
        SEARCH[atlas_search]
        GRAPH[atlas_graph]
        HTML[graph.html]
    end

    CODE --> IDX
    DOCS --> IDX
    IDX --> LDB
    IDX --> GRAFO
    LDB --> SEARCH
    GRAFO --> GRAPH
    GRAFO --> HTML
```

**Analogia:** pense no índice como um **catálogo de biblioteca** (busca por assunto)
e no grafo como um **mapa de referências cruzadas** (quem cita quem, quem importa quem).

---

## 2. Arquitetura do sistema

```mermaid
flowchart TB
    subgraph Cliente["Editor / Agente IA"]
        CURSOR[Cursor, Claude Code, Copilot…]
    end

    subgraph MCP["Servidor MCP (server.py)"]
        T1[atlas_search]
        T2[atlas_map]
        T3[atlas_graph]
        T4[atlas_index]
        T5[atlas_status]
    end

    subgraph Core["Núcleo Python"]
        IDX[indexer.py<br/>index_workspace]
        CHK[chunker.py<br/>ASTChunker]
        EMB[embeddings.py<br/>EmbeddingEngine]
        STO[storage.py<br/>StorageBackend]
        GRP[graph.py<br/>build_and_write]
    end

    subgraph Disco[".code-index/"]
        MAN[manifest.json]
        DB[lancedb/]
        GJ[graph.json]
        GH[graph.html]
    end

    CURSOR <-->|stdio JSON-RPC| MCP
    T4 --> IDX
    T1 --> STO
    T2 --> STO
    T3 --> GJ
    T5 --> MAN

    IDX --> CHK
    IDX --> EMB
    IDX --> STO
    IDX --> GRP
    STO --> DB
    STO --> MAN
    GRP --> GJ
    GRP --> GH
```

| Módulo | Papel |
|--------|-------|
| `indexer.py` | Orquestra varredura, hash, chunk, embed e persistência |
| `chunker.py` | Parse AST (Tree-sitter) → chunks por símbolo |
| `embeddings.py` | Vetores 384d com `all-MiniLM-L6-v2` (fastembed/ONNX) |
| `storage.py` | LanceDB + índice FTS (BM25) + manifest |
| `graph.py` | Deriva `graph.json` a partir do índice |
| `server.py` | Expõe tools MCP; resolve onde fica `.code-index/` |

---

## 3. Pipeline de indexação

A função central é `index_workspace()`. Ela executa **6 fases** com progresso no stderr:

```mermaid
flowchart TD
    A[1. Scan<br/>Varredura do workspace] --> B[2. Hash<br/>Detectar alterações]
    B --> C{Arquivo<br/>mudou?}
    C -->|Não| SKIP[Pula — reutiliza chunk existente]
    C -->|Sim| D[3. Chunk<br/>Parse AST Tree-sitter]
    D --> E[4. Embed<br/>Vetores fastembed]
    E --> F[5. Persist<br/>LanceDB + manifest]
    F --> G[6. Graph<br/>graph.json + graph.html]
    SKIP --> F
```

### Fase 1 — Scan

- Percorre o workspace (ou subpastas informadas em `--paths`).
- Ignora: `node_modules`, `.git`, `__pycache__`, arquivos ocultos, entradas em `.atlasignore`.
- Aceita extensões suportadas (Python, JS/TS, Go, Markdown, SQL, etc.).
- Descarta arquivos **> 2 MB**.
- Captura `mtime` e `size` de cada arquivo (otimização incremental).

### Fase 2 — Hash

- Compara cada arquivo com o `manifest.json` anterior.
- **Fast path:** se `mtime + size` iguais → reutiliza hash sha256 sem reler o disco.
- **Slow path:** calcula sha256 do conteúdo.
- Arquivos deletados são marcados para remoção do índice.

### Fase 3 — Chunk

- `ASTChunker` faz parse Tree-sitter e extrai **classes, funções, métodos**.
- Markdown vira chunks por **seção (heading)**.
- Sem símbolos AST → fallback para chunk `module` (arquivo inteiro).
- Extrai **imports** e **rationale refs** (`DECISAO-005`, `NOTE:`, wikilinks).

### Fase 4 — Embed

- Apenas chunks **novos ou alterados** são embedados (economia de tempo).
- Modelo: `sentence-transformers/all-MiniLM-L6-v2` (384 dimensões).
- Processamento em lotes de 32.

### Fase 5 — Persist

- Grava chunks no **LanceDB** com coluna vetorial + texto para FTS.
- Atualiza `manifest.json` (mapa arquivo → hash, metadados, git HEAD).
- Modo incremental: `delete` dos alterados + `append` dos novos.

### Fase 6 — Graph

- Reconstrói `graph.json` e gera `graph.html` autocontido.

### Como disparar a indexação

```bash
# Incremental (padrão) — só processa o que mudou
uv run atlas-index --workspace .

# Rebuild completo — ignora cache de hashes
uv run atlas-index --workspace . --full

# Subpastas específicas
uv run atlas-index --workspace . --paths src --paths docs
```

Via MCP: tool `atlas_index` (com opções `paths`, `full`, `dry_run`).

---

## 4. Artefatos em `.code-index/`

```mermaid
flowchart LR
    subgraph code-index[".code-index/"]
        M[manifest.json]
        L[lancedb/]
        G[graph.json]
        H[graph.html]
        R[background_reindex.log]
    end

    M -->|"metadados, hashes, git SHA"| STATUS[atlas_status]
    L -->|"vetores + FTS"| SEARCH[atlas_search]
    G -->|"conectividade"| GRAPH[atlas_graph]
    H -->|"visualização"| BROWSER[Navegador file://]
```

| Arquivo | Conteúdo | Usado por |
|---------|----------|-----------|
| `manifest.json` | Hashes, repos, idiomas, versão do índice, git HEAD | `atlas_status`, incremental |
| `lancedb/` | Tabela `chunks` com vetores e texto | `atlas_search`, `atlas_map` |
| `graph.json` | Nós (arquivos, símbolos, docs) e arestas (imports, cites…) | `atlas_graph` |
| `graph.html` | Viewer offline com pan/zoom e filtros | Humano (duplo-clique) |

> **Versão mínima:** grafo exige índice `2.1.0+`. Índices `2.0.0` ainda buscam,
> mas não têm `graph.json`.

---

## 5. Indexação incremental

A indexação incremental evita reprocessar arquivos inteiros a cada execução.

```mermaid
flowchart TD
    START[Novo atlas-index] --> LOCK{Lock livre?}
    LOCK -->|Não| SKIP[Retorna skipped_reason]
    LOCK -->|Sim| SCAN[Scan arquivos elegíveis]
    SCAN --> CMP{mtime+size<br/>== manifest?}
    CMP -->|Sim| FP[Fast path: reutiliza hash]
    CMP -->|Não| HASH[Calcula sha256]
    HASH --> EQ{hash == manifest?}
    EQ -->|Sim| FP
    EQ -->|Não| PROC[Chunk + Embed + Persist]
    FP --> UPD[Atualiza manifest]
    PROC --> UPD
    UPD --> DONE[Concluído]
```

| Flag | Efeito |
|------|--------|
| *(padrão)* | Incremental — só arquivos novos/alterados/removidos |
| `--full` | Ignora hashes; reprocessa tudo no escopo |
| `--paths src` | Restringe scan **e** remoções à subárvore `src/` |

**Staleness (`is_stale`):** compara o `git HEAD` do workspace com o gravado no
manifest. Só funciona se a **raiz do workspace** for um repositório git.

---

## 6. Pastas, subpastas e multi-repo

### Várias subpastas no mesmo workspace

**Sim** — repita `--paths`:

```bash
uv run atlas-index -w . -p frontend -p backend -p shared
```

```mermaid
flowchart TB
    WS[meu-workspace/]
    WS --> CI[.code-index/ — índice único]
    WS --> FE[frontend/]
    WS --> BE[backend/]
    WS --> SH[shared/]

    FE --> CI
    BE --> CI
    SH --> CI
```

Na busca, filtre por repo lógico com `path_prefix`:

```text
atlas_search(query="middleware auth", path_prefix="backend/")
```

### Workspace com vários repos git dentro

Funciona indexando a **pasta pai**. Limitações atuais:

| Aspecto | Comportamento |
|---------|---------------|
| Campo `repo` nos chunks | Nome da pasta workspace (`meu-workspace`), não `frontend`/`backend` |
| Filtro `repo` no MCP | Pouco útil — prefira `path_prefix` |
| `git_head_sha` | Só da raiz; repos filhos com `.git` próprio não alimentam staleness |
| **MCP e múltiplos `.code-index`** | **Um índice por processo** — não mergeia dois `.code-index` |

```mermaid
flowchart LR
    subgraph Suportado
        A[meu-workspace/.code-index]
        A --> B[frontend + backend + shared]
    end

    subgraph Não suportado
        C[frontend/.code-index]
        D[backend/.code-index]
        C -.-x MCP[MCP vê só um]
        D -.-x MCP
    end
```

**Recomendação:** um `.code-index` na raiz do workspace que engloba todos os repos.

---

## 7. Ferramentas MCP

```mermaid
mindmap
  root((Tools MCP))
    Descoberta
      atlas_search
      atlas_map
    Conectividade
      atlas_graph
    Operação
      atlas_index
      atlas_status
```

| Tool | Quando usar | Retorno típico |
|------|-------------|----------------|
| `atlas_search` | "Onde está X?" / "Como funciona Y?" | `file_path`, linhas, símbolo, score |
| `atlas_map` | Visão estrutural do projeto | Árvore de classes/funções |
| `atlas_graph` | Conectividade, hubs, caminhos | Nós, arestas, vizinhança |
| `atlas_status` | Diagnóstico do índice | stale?, chunks, `graph_viewer_path` |
| `atlas_index` | Criar/atualizar índice | stats ou job em background |

### Fluxo recomendado para agentes

```mermaid
sequenceDiagram
    participant Agente
    participant Search as atlas_search
    participant Graph as atlas_graph
    participant Read as Read (editor)

    Agente->>Search: query + path_prefix
    Search-->>Agente: metadados (file, lines, symbol)
    Agente->>Read: linhas exatas
    opt Pergunta sobre conexões
        Agente->>Graph: mode=explain, target=symbol
        Graph-->>Agente: docs citados, imports, rationale
    end
```

1. **`atlas_search`** com metadados only (`include_content=false`).
2. **`Read`** nas linhas retornadas.
3. **`atlas_graph(explain)`** se precisar de contexto arquitetural.
4. **`grep`** só para confirmar string literal exata.

---

## 8. Busca vs grafo — quando usar cada um

| Pergunta | Tool correta |
|----------|--------------|
| Onde está implementado `index_workspace`? | `atlas_search` |
| Quais decisões arquiteturais este código cita? | `atlas_graph(explain)` |
| Como o símbolo A se conecta ao doc B? | `atlas_graph(path)` |
| Quais notas/docs são centrais no projeto? | `atlas_graph(hubs)` |
| Estrutura de classes de um módulo | `atlas_map` |
| Explorar clusters visualmente | `graph.html` |

```mermaid
quadrantChart
    title Escolha da ferramenta
    x-axis Semântica --> Estrutural
    y-axis Localizar código --> Entender relações
    atlas_search: [0.25, 0.75]
    atlas_map: [0.75, 0.55]
    atlas_graph: [0.70, 0.25]
    graph.html: [0.85, 0.15]
```

---

## 9. Grafo de conhecimento

O grafo é **derivado** do índice — reconstruído a cada indexação. Não é busca
semântica; é um mapa de **relações explícitas** extraídas do código e dos docs.

### Tipos de nó

```mermaid
erDiagram
    FILE ||--o{ SYMBOL : contains
    FILE ||--o{ SECTION : contains
    DOC ||--o{ SECTION : contains
    SYMBOL ||--o{ RATIONALE : annotates
    SYMBOL }o--o{ DOC : cites
    SECTION }o--o{ DOC : links_to
    FILE }o--o{ FILE : imports

    FILE {
        string kind "file"
        string file_path
    }
    DOC {
        string kind "doc"
        string file_path ".md"
    }
    SYMBOL {
        string kind "symbol"
        string scope_name
        int start_line
    }
    SECTION {
        string kind "section"
        string heading
    }
    RATIONALE {
        string kind "rationale"
        string note_text
    }
```

| `kind` | Exemplo de id | Origem |
|--------|---------------|--------|
| `file` | `file:src/app.py` | Todo arquivo indexado |
| `doc` | `file:docs/dec-001.md` | Arquivo `.md` |
| `symbol` | `sym:src/app.py#login` | Função/classe/método |
| `section` | `sec:README.md#Instalação` | Heading markdown |
| `rationale` | `rat:a1b2c3…` | Comentário `NOTE:` / `WHY:` |

### Tipos de aresta

| `kind` | Significado | Exemplo |
|--------|-------------|---------|
| `contains` | Arquivo contém símbolo/seção | `indexer.py` → `index_workspace` |
| `imports` | Import Python ou JS/TS relativo | `server.py` → `storage.py` |
| `links_to` | Link/wikilink em markdown | `index.md` → `dec-002.md` |
| `cites` | Referência `DECISAO-005` no código | função → doc de decisão |
| `annotates` | Comentário rationale no símbolo | `# WHY: …` → nó rationale |

### Tool `atlas_graph` — três modos

#### `hubs` — nós mais conectados

Encontra documentos e código “centrais” (maior grau de conexão).

```text
atlas_graph(mode="hubs", top_n=10)
```

Útil para: *"Por onde começo a entender este projeto?"*

#### `explain` — vizinhança de um nó

```text
atlas_graph(mode="explain", target="index_workspace")
```

Retorna vizinhos agrupados por tipo: arquivos, docs citados, rationale.

O `target` aceita:
- nome do símbolo (`StorageBackend`)
- caminho de arquivo (`src/codesteer_atlas/server.py`)
- sufixo único (`dec-002-resolucao-index-dir.md`)

#### `path` — caminho entre dois nós

Busca em largura (BFS), até 10 saltos:

```text
atlas_graph(
  mode="path",
  source="index_workspace",
  target="dec-002-resolucao-index-dir.md"
)
```

Exemplo de caminho real:

```mermaid
flowchart LR
    A[index_workspace] -->|cites| B[dec-001-busca-hibrida-rrf.md]
    B -->|links_to| C[index.md — seção decisions]
    C -->|links_to| D[dec-002-resolucao-index-dir.md]
```

---

## 10. Visualizador `graph.html`

Após indexar, abra o arquivo gerado:

```bash
# macOS
open .code-index/graph.html

# Linux
xdg-open .code-index/graph.html
```

Ou use o caminho em `atlas_status` → `graph_viewer_path`.

```mermaid
flowchart TB
    subgraph Sidebar
        S1[Busca por label/id]
        S2[Filtros de nó e aresta]
        S3[Painel de detalhes]
    end

    subgraph Canvas
        C1[Pan / zoom / clique]
        C2[Foco no subgrafo local]
    end

    S1 --> C2
    C1 --> S3
```

| Controle | Ação |
|----------|------|
| Campo de busca | Filtra nós por label ou id |
| Clique em nó | Foca subgrafo local + detalhes |
| Filtros | Mostra/oculta tipos de nó e aresta |
| Recentralizar | Volta ao layout inicial |
| Limpar foco | Remove seleção |
| Expandir tudo | Aparece em grafos grandes (> 3000 nós) |

**Modo hubs-only:** grafos muito grandes abrem resumidos nos hubs centrais.
Use "Expandir tudo" se precisar ver mais.

**Debug:** adicione `?debug=1` na URL para estatísticas extras.

---

## 11. Enriquecendo o grafo

Quanto mais referências explícitas no código e nos docs, mais denso fica o grafo.

### No código — cites arquiteturais

```python
# DECISAO-005: embeddings locais com fastembed
def encode(self, texts: list[str]) -> list[list[float]]:
    ...
```

Padrões reconhecidos: `DECISAO-003`, `DEC-002`, `ADR-001`, `RFC-012`.

### No código — anotações rationale

```python
# WHY: stdio deve permanecer limpo para o canal MCP JSON-RPC
# NOTE: redirect acontece antes de importar lancedb
```

### Em markdown — links e wikilinks

```markdown
Ver [[dec-002-resolucao-index-dir]] para resolução do índice.

Consulte [indexação incremental](../decisions/dec-003-indexacao-incremental.md).
```

### Imports (automático)

- **Python:** `from codesteer_atlas.storage import StorageBackend`
- **JS/TS:** imports relativos `./utils` ou `../lib`

Após adicionar referências, reindexe:

```bash
uv run atlas-index --workspace .
```

---

## 12. Fluxos práticos passo a passo

### Primeira vez no projeto

```mermaid
flowchart TD
    S1[./setup.sh ou uv sync] --> S2[uv run atlas-index --workspace .]
    S2 --> S3[uv run python deploy_mcp.py]
    S3 --> S4[Reiniciar editor MCP]
    S4 --> S5[atlas_status — confirmar index_exists]
    S5 --> S6[Opcional: open .code-index/graph.html]
```

### Explorar uma feature desconhecida

1. `atlas_search(query="autenticação JWT", path_prefix="src/")`
2. `Read` nos hits com melhor score.
3. `atlas_graph(mode="explain", target="AuthMiddleware")`
4. Ler docs citados (`dec-xxx.md`) retornados pelo grafo.

### Atualizar após mudanças grandes

1. `atlas_status` → se `is_stale: true`, rodar `atlas_index`.
2. Ou direto: `uv run atlas-index --workspace .`
3. Reindex completo raro: `--full` apenas se manifest corrompido ou upgrade de versão.

### Workspace multi-repo

1. Abrir a **pasta pai** no editor (não multi-root com índices separados).
2. `uv run atlas-index --workspace /caminho/meu-workspace`
3. Buscar com `path_prefix="nome-do-repo/"`.

---

## 13. Perguntas frequentes

**Preciso reindexar a cada commit?**
Não necessariamente. A indexação incremental detecta arquivos alterados por hash.
Use `atlas_status` ou reindexe quando a busca parecer desatualizada.

**Posso indexar só `src/`?**
Sim: `--paths src`. O restante do índice permanece intacto.

**O MCP funciona offline?**
Sim. Embeddings e LanceDB são 100% locais. Nenhum código sai da máquina.

**Por que `atlas_graph` falha com "graph.json não encontrado"?**
O índice foi criado antes da versão `2.1.0` ou a indexação não completou a fase
Graph. Rode `uv run atlas-index --workspace . --full`.

**Posso ter dois `.code-index` ativos no MCP?**
Não na mesma instância. O servidor resolve **um** índice por processo. Use índice
unificado na pasta pai ou registre servidores MCP separados com `ATLAS_INDEX_DIR`
diferente (configuração avançada).

**Qual a diferença entre `atlas_search` e `grep`?**
`atlas_search` entende **conceitos** ("middleware de autenticação").
`grep` encontra **strings exatas** (`def authenticate(`). Use Atlas primeiro;
grep para confirmar literais.

---

## Referências

- [README](../README.md) — instalação e início rápido
- [Documentação visual](index.html) — conceitos MCP e busca híbrida
- [CLAUDE.md](../CLAUDE.md) — arquitetura técnica para agentes
- Código-fonte: `src/codesteer_atlas/indexer.py`, `graph.py`, `server.py`

---

*Última atualização: julho/2026 · CodeSteer Atlas 2.1.0+*
