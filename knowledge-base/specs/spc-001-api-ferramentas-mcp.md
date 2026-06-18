---
id: spc-001
type: api
title: "API das ferramentas MCP atlas_*"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: sys-005
    rel: depends-on
  - id: dec-001
    rel: depends-on
tags: [mcp, api, contrato]
source: greenfield
migration_status: ""
meta:
  module: "src/codesteer_atlas/server.py"
---

# API das ferramentas MCP atlas_*

## Contexto

Contrato das quatro ferramentas expostas pelo [[sys-005-mcp-server]]. Transporte
[[meta/glossary#stdio-transport|stdio]]; respostas em JSON compacto (string).

Recurso read-only adicional: `atlas://status` (alias de `atlas_status`).

## Endpoints

### `atlas_search`

Busca híbrida ([[dec-001-busca-hibrida-rrf]]) — **primeira ferramenta** para
localizar código ou documentos. Não chamar `atlas_status` antes "só para checar".

| Parâmetro | Tipo | Obrigatório | Default | Descrição |
| --------- | ---- | ----------- | ------- | --------- |
| `query` | string | sim | — | Linguagem natural ou símbolo exato |
| `top_k` | int | não | 5 | Máximo de resultados (1–50) |
| `limit` | int | não | — | Alias de `top_k` (sobrescreve se informado) |
| `repo` | string | não | — | Filtro por repositório |
| `language` | string | não | — | Ex.: `python`, `javascript` |
| `path_prefix` | string | não | — | Ex.: `src/codesteer_atlas` |
| `include_content` | bool | não | `false` | Incluir campo `content` nos resultados |

**Resposta 200 (JSON):**

```json
{
  "results": [
    {
      "file_path": "src/example.py",
      "lines": [10, 42],
      "symbol": "MyClass.method",
      "type": "method",
      "language": "python",
      "score": 0.031,
      "repo": "my-repo",
      "content": "...",
      "markdown_references": []
    }
  ],
  "total_chunks_searched": 835,
  "query_time_ms": 467.19
}
```

`content` só presente se `include_content=true`. Resultados markdown podem incluir
`markdown_references` com `{file_path, anchor, resolved_section, alias?, candidates?}`.

### `atlas_map`

Mapa hierárquico compacto de arquivos e símbolos (sem conteúdo).

| Parâmetro | Tipo | Default | Descrição |
| --------- | ---- | ------- | --------- |
| `repo` | string | — | Filtro por repo (informativo na resposta) |
| `path_prefix` | string | — | Filtra `file_path` por prefixo |
| `max_depth` | int | 3 | Profundidade máxima de diretórios |
| `query` | string | — | Aceito por compatibilidade; **ignorado** |

**Resposta:**

```json
{
  "map": "src/server.py\n  class Foo\n  func bar",
  "total_files": 42,
  "total_symbols": 318,
  "repo": "all"
}
```

### `atlas_status`

Diagnóstico do índice. **Não** é pré-requisito de `atlas_search`/`atlas_map`.

| Parâmetro | Tipo | Descrição |
| --------- | ---- | --------- |
| _(nenhum)_ | — | — |

**Resposta (índice existente):**

```json
{
  "index_exists": true,
  "total_chunks": 835,
  "repos_indexed": ["codesteer-atlas"],
  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "embedding_backend": "fastembed",
  "storage_backend": "lancedb",
  "index_path": "/path/.code-index",
  "index_resolution": "discovery",
  "last_indexed_at": "2026-06-17T12:00:00",
  "git_head_sha": "abc123",
  "is_stale": false,
  "languages_indexed": ["python"],
  "reindexing": false
}
```

Valores de `index_resolution`: `cli-arg` | `env` | `discovery` | `editor-project-dir` |
`roots` | `roots-fallback` | `editor-project-dir-fallback` | `cwd-fallback`.

### `atlas_index`

Indexa ou reindexa o workspace ([[sys-004-index-workspace]]).

| Parâmetro | Tipo | Default | Descrição |
| --------- | ---- | ------- | ----------- |
| `workspace` | string | pai do índice ou CWD | Caminho absoluto do workspace |
| `paths` | string[] | — | Subpastas relativas; omitido = workspace inteiro |
| `full` | bool | `false` | Ignora hashes; rebuild completo |
| `dry_run` | bool | `false` | Lista candidatos sem indexar |

**Modos de execução:**

| Cenário | Comportamento |
| ------- | ------------- |
| `dry_run=true` | Retorna `candidates` + `total_eligible_files` |
| `full=true` ou `paths` omitido | **Assíncrono** (subprocesso); poll via `atlas_status.reindexing` |
| `paths` não vazio e `full=false` | **Síncrono**; retorna `IndexStats` |

**Resposta síncrona:**

```json
{
  "workspace": "/path",
  "indexed_paths": ["src"],
  "files_processed": 12,
  "files_skipped_unchanged": 340,
  "files_removed": 1,
  "chunks_persisted": 89,
  "duration_s": 4.2,
  "git_head_sha": "abc123"
}
```

## Modelo de Erros

| Situação | Comportamento |
| -------- | ------------- |
| Índice ausente (`atlas_search`/`atlas_map`) | Erro acionável com instrução para `atlas_index` |
| `query` vazio | `ValueError` |
| `top_k` fora de 1–50 | `ValueError` |
| `path` fora do workspace (`atlas_index`) | `ValueError` (anti-traversal) |
| Manifest incompatível | `RuntimeError` pedindo reindex — ver [[ops-003-runbook-manifest-incompativel]] |
| Lock de reindex ativo | `skipped_reason: "reindex_in_progress"` — ver [[ops-004-runbook-reindex-em-progresso]] |
| Workspace inválido | `ValueError` |

## Exemplos de uso (agente)

```text
# Passo 1 — localizar (metadados apenas)
atlas_search(query="resolve index dir", path_prefix="src/codesteer_atlas")

# Passo 2 — ler linhas exatas no editor
Read file_path + lines retornados

# Passo 3 — mapa estrutural
atlas_map(path_prefix="src/codesteer_atlas", max_depth=2)

# Diagnóstico explícito
atlas_status() → se is_stale: atlas_index(paths=["src"])
```

## Notas Relacionadas

- [[gd-030-primeiros-passos]] — setup e fluxo diário
- [[ops-001-runbook-reindex]] — procedimento operacional de reindex
- [[ops-002-runbook-indice-stale]] — quando `is_stale: true`

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
