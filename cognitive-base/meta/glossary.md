# Glossário

> Termos do domínio e da arquitetura do **CodeSteer Atlas**. Cada entrada tem uma
> âncora `#termo` para ser linkada via `[[meta/glossary#termo|termo]]` nas notas.
> Não defina termos inline nas notas — link aqui.

## A

### atomicidade {#atomicidade}

Propriedade de uma operação que ou completa por inteiro ou não produz efeito
parcial. No contexto de indexação, garante que o índice não fica em estado
intermediário inconsistente após falhas.

## B

### BM25 {#bm25}

Algoritmo de busca lexical por relevância de termos. No Atlas, complementa a
busca vetorial na fusão híbrida.

## C

### chunk {#chunk}

Unidade mínima indexável extraída do AST (classe, função, método ou módulo
inteiro como fallback).

## E

### embedding {#embedding}

Representação vetorial densa do texto de um chunk, gerada localmente pelo
`fastembed` (`all-MiniLM-L6-v2`, 384 dimensões).

## F

### FTS {#fts}

Full-Text Search — busca textual sobre o conteúdo dos chunks no LanceDB.

## H

### busca híbrida {#busca-hibrida}

Combinação de similaridade vetorial (cosseno) e BM25, fundida via Reciprocal
Rank Fusion (RRF).

## I

### idempotência {#idempotencia}

Repetir a mesma operação de indexação sobre arquivos inalterados não altera o
resultado — hashes sha256 no manifest decidem o que reindexar.

### indexação incremental {#indexacao-incremental}

Estratégia que reprocessa apenas arquivos novos ou alterados, removendo chunks
obsoletos antes de anexar os novos.

## L

### LanceDB {#lancedb}

Banco de dados vetorial embedded usado pelo `StorageBackend` para armazenar
chunks, embeddings e índice FTS local em `.code-index/`.

## M

### MCP {#mcp}

Model Context Protocol — protocolo pelo qual editores (Cursor, Claude Desktop)
invocam ferramentas do Atlas (`atlas_search`, `atlas_map`, etc.) via stdio.

### manifest {#manifest}

Arquivo `manifest.json` em `.code-index/` com metadados do índice, versão e
hashes por arquivo.

## R

### RRF {#rrf}

Reciprocal Rank Fusion — técnica de fusão de rankings de busca vetorial e
lexical em um único score.

## S

### stdio transport {#stdio-transport}

Canal de comunicação do servidor MCP via stdin/stdout; exige que logs e
warnings não poluam stdout.

### símbolo {#simbolo}

Entidade AST indexável: classe, função ou método identificado pelo Tree-sitter.

## T

### Tree-sitter {#tree-sitter}

Parser incremental usado pelo `ASTChunker` para extrair chunks em granularidade
de símbolo.
