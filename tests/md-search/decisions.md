# Decisão 007: Índice Local

O índice de busca é armazenado localmente em `.code-index/`, usando um banco
de dados embutido (LanceDB). Essa decisão evita dependências externas e garante
que nenhum dado do código-fonte saia da máquina do usuário.

Veja também a [Visão Geral](overview.md) para o contexto completo do fluxo.

# Decisão 008: Backend de Embeddings

Os embeddings são gerados localmente via `fastembed` (modelo `all-MiniLM-L6-v2`,
384 dimensões), sem depender de chamadas a serviços externos de IA.

## Alternativas Consideradas

- `sentence-transformers` com PyTorch: descartado por aumentar significativamente
  o tamanho da instalação e o tempo de startup.
- APIs de embedding remotas: descartadas por violar o princípio de execução 100% local.
