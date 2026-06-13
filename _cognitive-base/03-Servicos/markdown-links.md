---
tipo: servico
titulo: "markdown_links"
arquivo: "src/codesteer_atlas/markdown_links.py"
dominio: "[[indexacao]]"
tags: [servico, markdown]
contrato: "list[MarkdownReference]"
criado: 2026-06-13
---

# markdown_links

## Propósito

Extrai alvos de links markdown em chunks para enriquecer [[atlas-search]] com `references`.

## Funções Públicas

- `extract_markdown_link_targets(content, source_path)`
- `slugify_heading(text)`

## Contrato de Retorno

Lista de refs com `file_path`, `anchor`, `resolved_section`; ignora HTTP externos.

## Dependências

- Usado por indexer e [[atlas-search]]

## Comportamento Offline

Parse local de markdown.

## Testes

- `test_markdown_links.py` (ver [[server-testes]] para integração search)
