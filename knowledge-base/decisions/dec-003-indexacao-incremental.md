---
id: dec-003
type: adr
title: "Indexação incremental por hash sha256 de arquivos"
status: draft
created: "2026-06-17"
updated: "2026-06-17"
author: "@luiscarloslopes"
links:
  - id: sys-004
    rel: related-to
tags: [indexacao, performance]
source: greenfield
migration_status: ""
meta: {}
---

# Indexação incremental por hash sha256 de arquivos

## Contexto

Reindexar o workspace inteiro a cada mudança pequena é lento e desperdiça CPU
em embeddings. Projetos grandes exigem [[meta/glossary#idempotencia|idempotência]]
na indexação.

## Decisão

`index_workspace()` compara sha256 por arquivo contra `manifest.files`:

- **Inalterado** → skip
- **Alterado/novo** → remove chunks antigos (`delete_by_file_paths`), gera novos, append
- **Removido** → delete dos chunks correspondentes
- **Sem manifest ou `--full`** → overwrite completo da tabela

## Alternativas Consideradas

| Alternativa | Contras |
| ----------- | ------- |
| Sempre full rebuild | Lento em monorepos |
| mtime apenas | Falsos negativos com git checkout |
| **sha256 de conteúdo** | Hash por arquivo; custo aceitável |

## Consequências

- Manifest atualizado via `update_manifest_after_incremental`
- `atlas_status.is_stale` compara git HEAD indexado vs atual
- Testes em `tests/test_indexer.py` cobrem caminhos incremental e full

## Notas Relacionadas

- [[sys-004-index-workspace]] — implementação
- [[meta/glossary#manifest|manifest]] — estrutura de metadados

## Histórico

| Versão | Data       | Autor            | Descrição |
| ------ | ---------- | ---------------- | --------- |
| 1.0.0  | 2026-06-17 | @luiscarloslopes | Criação   |
