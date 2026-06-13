---
tipo: qualidade
titulo: "chunker-testes"
arquivo: "tests/test_chunker.py"
tags: [testes, chunker]
dominio: "[[indexacao]]"
criado: 2026-06-13
---

# chunker-testes

## Escopo

`tests/test_chunker.py` — multi-linguagem e edge cases.

## Cenários cobertos

- Python classes/functions + fallback module
- Java, C#, Dart/Flutter, JSX, XML
- Markdown headings, SQL statements, plain text
- Truncation de chunks oversized
- SQL unparseable → fallback text

## Relacionados

- [[ast-chunker]]
- [[tree-sitter]]
