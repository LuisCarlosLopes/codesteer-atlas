# CodeSteer Atlas Constitution

## Core Principles

### I. Execução 100% Local e Privada
O sistema processa tudo localmente na máquina do desenvolvedor. A geração de embeddings usando o modelo `all-MiniLM-L6-v2` e o armazenamento/busca no banco LanceDB embutido funcionam de forma independente e totalmente offline, garantindo que o código-fonte proprietário nunca seja enviado para serviços de nuvem ou exposto a terceiros.

### II. Indexação Sintática via AST (Tree-sitter)
O código do workspace deve ser processado estruturalmente por meio de analisadores sintáticos Tree-sitter para mapear classes, métodos e funções. Isso garante que o contexto fornecido aos agentes de IA seja quebrado e isolado em escopos lógicos reais (chunks de símbolos), e não em blocos arbitrários de linhas ou caracteres.

### III. Eficiência de Contexto e Busca Híbrida Inteligente
Prioridade absoluta na economia de tokens dos prompts da IA. A recuperação de contexto deve ser baseada em busca híbrida (similaridade vetorial combinada com busca lexical BM25, fundidos pelo algoritmo RRF), retornando estritamente os trechos mais relevantes do código para evitar o estouro de contexto e o fenômeno "lost in the middle".

### IV. Isolamento de Stdio e Resiliência da Interface MCP
Toda a comunicação externa com editores e clientes de IA é feita via stdio (JSON-RPC) por meio do FastMCP. Qualquer tipo de warning de dependências, C-extensions, logging ou prints indesejados deve ser obrigatoriamente isolado e redirecionado para o canal `stderr`, mantendo o `stdout` livre de ruídos para garantir a integridade do protocolo.

### V. Simplicidade Operacional e Fricção Mínima (YAGNI)
O design prioriza simplicidade e portabilidade, rodando nativamente em macOS e Linux via Python e gerenciador `uv`. Funcionalidades acessórias complexas, como sincronização automática contínua (watch mode), interfaces Web de administração ou reranking externo na nuvem, estão fora do escopo para assegurar baixo acoplamento e inicialização instantânea.

## Restrições Adicionais e Padrões de Código

### Comunicação Concisa e Economia de Contexto
Agentes e revisores devem se comunicar de maneira extremamente concisa. É proibido gerar relatórios longos, sumários de processo redundantes ou recaps de instruções, exceto sob solicitação direta do usuário. Cada palavra e token de contexto economizados importam para a performance e custo de uso da IA.

### Comentários de Código e Documentação Inline
Comentários no código e tags de contexto (como `// @MindContext` ou `// @Mind...`) devem ser concisos, escritos em português (conforme regras do usuário), e seguir estritamente as diretrizes da skill `codesteer-tagger`:
- Apenas documentar o que não é óbvio a partir da leitura direta do código.
- Usar tags apenas quando agregarem valor real de governança ou contexto.
- Evitar redundâncias e ruído (em geral, manter de 1 a 3 tags por unidade lógica).

## Workflow de Qualidade e Desenvolvimento

### Garantia de Qualidade com Testes Automatizados
Qualquer alteração de comportamento lógico no indexador ou no servidor MCP deve vir acompanhada de testes unitários ou de integração apropriados. A suíte de testes deve ser executada com o comando:
```bash
uv run --python 3.12 --with pytest python -m pytest
```

### Estilo de Código e Linter
O código Python deve seguir as convenções de estilo e conformidade do repositório. O linter `ruff` deve ser executado para validação do código alterado:
```bash
uv run ruff check
```

## Governance

- Esta Constituição é o documento de maior relevância normativa no repositório. As regras e princípios aqui estabelecidos prevalecem sobre quaisquer convenções locais, padrões locais ou regras temporárias definidas na memória operacional (`operational-memory.md`) se houver conflito ou ambiguidade.
- Qualquer alteração a esta Constituição exige o incremento de sua versão, atualização da data de alteração e registro dos novos princípios.

**Version**: 1.0.0 | **Ratified**: 2026-06-05 | **Last Amended**: 2026-06-05
