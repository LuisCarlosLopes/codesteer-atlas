---
tipo: servico
titulo: "index_workspace"
arquivo: "src/codesteer_atlas/indexer.py"
dominio: "[[indexacao]]"
tags: [servico, indexacao]
contrato: "IndexStats"
criado: 2026-06-13
---

# index_workspace

## Propósito

Núcleo reutilizável de indexação ([[ADR-005-indexacao-incremental]]): varre workspace, compara hashes, chunka, embeda e persiste.

## Funções Públicas

- `index_workspace(workspace, index_dir, paths, full, dry_run, ...) → IndexStats`
- `should_ignore(path, spec)` — regras + `.atlasignore`
- `load_atlasignore_spec(workspace)`
- `get_git_head_sha(workspace)`
- `cli()` — entrypoint `atlas-index`

## Contrato de Retorno

`IndexStats` com `files_processed`, `files_skipped_unchanged`, `chunks_persisted`, `skipped_reason`.

## Dependências

- [[ast-chunker]]
- [[embedding-engine]]
- [[storage-backend]]
- [[reindex-lock]]

## Comportamento Offline

100% local — sem rede.

## Testes

- [[indexer-testes]]
