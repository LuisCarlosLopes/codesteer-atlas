---
tipo: qualidade
titulo: "indexer-testes"
arquivo: "tests/test_indexer.py"
tags: [testes, indexacao]
dominio: "[[indexacao]]"
criado: 2026-06-13
---

# indexer-testes

## Escopo

`tests/test_indexer.py` — núcleo de indexação.

## Cenários cobertos

- Incremental: skip unchanged, delete removed files
- `--full` rebuild
- `.atlasignore` patterns (glob, negation, anchored)
- Partial `paths` preserva outras pastas
- Lock externo → skip
- `files_meta` mtime/size optimization
- `get_git_head_sha` edge cases (Windows, não-git)
- CLI `atlas-index` run

## Relacionados

- [[index-workspace]]
- [[reindex-lock]]
