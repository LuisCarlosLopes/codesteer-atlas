---
tipo: servico
titulo: "ASTChunker"
arquivo: "src/codesteer_atlas/chunker.py"
dominio: "[[indexacao]]"
tags: [servico, chunker, tree-sitter]
contrato: "list[CodeChunk]"
criado: 2026-06-13
---

# ASTChunker

## Propósito

Parseia arquivos com [[tree-sitter]] e extrai chunks em granularidade de classe/função/método.

## Funções Públicas

- `chunk_file(path, content, repo) → list[CodeChunk]`
- Fallback: chunk de módulo inteiro quando sem parser/símbolos
- Suporte especial: markdown (headings), SQL, texto plano

## Contrato de Retorno

Lista de [[code-chunk]] com `scope_type`, `scope_name`, linhas 1-indexed.

## Dependências

- `tree_sitter_language_pack`
- `config.SUPPORTED_EXTENSIONS`, `MAX_TOKENS_PER_CHUNK`

## Comportamento Offline

Parsers locais embutidos.

## Testes

- [[chunker-testes]] — Python, Java, C#, SQL, MD, JSX, XML, Dart
