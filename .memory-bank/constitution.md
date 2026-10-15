# CodeSteer Atlas Constitution

## Core Principles

### I. Local-First por Padrão, Fronteira de Dados Declarada
Na configuração padrão, o sistema processa tudo localmente na máquina do desenvolvedor: a geração de embeddings e o armazenamento/busca no LanceDB embutido funcionam de forma independente e totalmente offline, e nenhum código-fonte sai do host.

Camadas de enriquecimento que dependem de um modelo de linguagem (descrição semântica de símbolos, sumarização hierárquica) são **opcionais, desligadas por padrão e explicitamente habilitadas** pelo usuário. Quando habilitadas, valem três regras invioláveis:

1. **Ordem de preferência de origem:** `sampling` do próprio cliente MCP → modelo local (Ollama, llama.cpp, endpoint compatível) → API externa. A primeira opção não amplia nenhuma fronteira de dados, porque o código já está no contexto do agente que fez a chamada.
2. **A fronteira é declarada e inspecionável.** `atlas_status` reporta qual origem está ativa e o que é enviado. Nunca há envio silencioso.
3. **Degradação, não falha.** Sem origem configurada, o Atlas opera na camada estrutural — completo e correto, apenas sem a camada semântica.

O modo 100% offline permanece um caminho suportado e testado, não um resíduo de compatibilidade.

### II. Estrutura Antes de Heurística
O código do workspace é processado estruturalmente para mapear classes, métodos e funções, garantindo que o contexto entregue ao agente seja isolado em escopos lógicos reais — nunca em blocos arbitrários de linhas.

A resolução de relações entre símbolos segue uma hierarquia de qualidade, e a origem é registrada por aresta:

1. **Índice semântico do toolchain** (SCIP/LSIF via `scip-python`, `scip-typescript`, `rust-analyzer`, `gopls`) quando disponível — resolução com precisão de compilador.
2. **AST via Tree-sitter** como fallback — resolução sintática, suficiente para estrutura e imports, aproximada para chamadas.

É proibido reimplementar inferência de tipo ou resolução de nomes à mão quando um índice do toolchain pode fornecê-la. Onde apenas o fallback existe, a aresta carrega sua confiança e o consumidor sabe o que está pisando.

### III. Eficiência de Contexto como Pós-Condição
Prioridade absoluta na economia de tokens. Duas exigências derivam disso:

**O pipeline de recuperação** é composto por estágios substituíveis — recall multi-braço (vetorial, léxico, estrutural, semântico), reordenação, e montagem final — e **nenhum estágio entra em produção sem medição no golden set, agregada por classe de query**. Média global que esconde regressão em uma classe não é evidência.

**Toda resposta tem teto de token declarado em constante**, aplicado como pós-condição no serializador, não como intenção do chamador. Nenhuma resposta cresce com o tamanho do repositório.

A superfície de tools é modelada pela **tarefa do agente**, não pela estrutura interna do índice: quando uma pergunta recorrente exige compor três chamadas, a composição pertence ao servidor, onde é determinística e cabe em um orçamento.

### IV. Isolamento de Stdio e Resiliência da Interface MCP
Toda a comunicação externa com editores e clientes de IA é feita via stdio (JSON-RPC) por meio do FastMCP. Qualquer tipo de warning de dependências, C-extensions, logging ou prints indesejados deve ser obrigatoriamente isolado e redirecionado para o canal `stderr`, mantendo o `stdout` livre de ruídos para garantir a integridade do protocolo.

Esta regra se estende às chamadas de `sampling` do Princípio I: uma requisição ao cliente não pode, em nenhuma circunstância, contaminar o canal de protocolo.

### V. Portabilidade Multiplataforma e Inicialização Instantânea
O MCP deve rodar nativamente em **macOS, Linux e Windows** via Python e gerenciador `uv`. Bootstrap (`setup.sh`, `setup.ps1`), resolução de paths, subprocessos, locking de arquivos, encoding de stdio e registro em clientes MCP (`deploy_mcp.py`) devem tratar diferenças de plataforma de forma explícita e coberta por testes — sem assumir ambiente POSIX.

O caminho de inicialização do servidor permanece livre de trabalho pesado: modelos, índices e dependências de custo alto são carregados de forma preguiçosa, na primeira chamada que realmente os exige. Funcionalidade acessória pode existir — o que não pode é atrasar o startup.

### VI. Degradação Explícita
Quando um braço de busca falha, um índice está incompleto, uma linguagem não tem resolver, uma resposta foi truncada ou uma camada opcional está desligada, **o chamador é informado**. Resultado silenciosamente pior é o modo de falha mais caro do sistema: o agente age com confiança sobre uma resposta incompleta.

Mecanismos existentes que materializam este princípio — `SearchOutcome.warnings`, `SearchResult.match_arms`, `IndexStats.brief_status` — são o padrão a seguir, não exceções. Toda capacidade nova declara como ela degrada.

## Restrições Adicionais e Padrões de Código

### Artefatos de Inspeção Humana
O Atlas é consumido por agentes, mas é **operado, auditado e depurado por pessoas**. `graph.html` (gerado por `viewer.py`, autocontido e offline) existe para inspeção humana do grafo e permanece um artefato de primeira classe: não deve ser removido sob o argumento de que um agente não o consome. Decisão registrada em 2026-08-30 após proposta explícita de remoção, rejeitada.

O mesmo critério vale para artefatos futuros de diagnóstico visual.

### Comunicação Concisa e Economia de Contexto
Agentes e revisores devem se comunicar de maneira extremamente concisa. É proibido gerar relatórios longos, sumários de processo redundantes ou recaps de instruções, exceto sob solicitação direta do usuário. Cada palavra e token de contexto economizados importam para a performance e custo de uso da IA.

### Comentários de Código e Documentação Inline
Comentários no código e tags de contexto (como `// @MindContext` ou `// @Mind...`) devem ser concisos, escritos em português (conforme regras do usuário), e seguir estritamente as diretrizes da skill `codesteer-tagger`:
- Apenas documentar o que não é óbvio a partir da leitura direta do código.
- Usar tags apenas quando agregarem valor real de governança ou contexto.
- Evitar redundâncias e ruído (em geral, manter de 1 a 3 tags por unidade lógica).

### Ambiguidade Exige Parada
Pedido ambíguo, com mais de uma interpretação plausível ou com premissas não fornecidas deve ser interrompido antes de executar. O agente nomeia a ambiguidade de forma objetiva e pergunta. Normalizar internamente e prosseguir sem clarificação é proibido. Suposições assumidas são declaradas explicitamente em seção "Hipóteses" no artefato.

### Código Mínimo — Sem Especulação
Código gerado contém apenas o que foi pedido. Proibido: features não solicitadas, abstrações para uso único, "flexibilidade" ou "configurabilidade" não requeridas, error handling para cenários impossíveis. Se uma solução com menos código resolve o problema, ela é preferida.

### Mudanças Cirúrgicas
Cada linha alterada deve ser rastreável diretamente ao pedido. Código adjacente não é tocado, reformatado nem "melhorado". Estilo e convenções existentes são preservados. Imports, variáveis e funções tornados órfãos pelas próprias mudanças são removidos. Dead code pré-existente é mencionado, nunca deletado sem pedido explícito.

### Execução Orientada a Critério Verificável
Toda task deve ter critério de aceite observável antes de iniciar execução. Critérios vagos ("fazer funcionar", "melhorar") são rejeitados para clarificação. Para tasks com múltiplos passos, cada passo declara sua verificação: `[passo] → verificar: [condição]`.

## Workflow de Qualidade e Desenvolvimento

### Garantia de Qualidade com Testes Automatizados
Qualquer alteração de comportamento lógico no indexador ou no servidor MCP deve vir acompanhada de testes unitários ou de integração apropriados. Mudanças com impacto multiplataforma devem incluir cobertura ou validação explícita para Windows quando aplicável. A suíte de testes deve ser executada com:

```bash
uv run --python 3.12 --with pytest python -m pytest
```

### Medição de Ranking
Alteração em qualquer estágio do pipeline de recuperação exige rodar o harness de avaliação e comparar com a baseline versionada, por classe de query:

```bash
uv run python scripts/eval_search.py --baseline tests/eval/baseline.json
```

Regressão em qualquer classe bloqueia a mudança até ser justificada ou corrigida. Mudanças de ranking entram atrás de flag de ambiente antes de virarem padrão.

### Estilo de Código e Linter
O código Python deve seguir as convenções de estilo e conformidade do repositório:

```bash
uv run ruff check
```

### Changelog Sempre Atualizado
Toda mudança relevante do produto é registrada em `CHANGELOG.md` **no mesmo conjunto de alterações** que a produz — não em commit posterior e não "depois do merge". O arquivo segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e [Versionamento Semântico](https://semver.org/lang/pt-BR/). Entrega que se enquadra abaixo e omite o changelog está incompleta.

1. **Onde.** Entradas novas vão sob `[Unreleased]`, na categoria correta (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`). O corte de versão move o bloco para `[X.Y.Z] - YYYY-MM-DD` e deixa `[Unreleased]` vazio.
2. **O quê.** Comportamento observável por usuário ou agente: tools, flags, contratos, `index_version`, degradação, CLI, correções e breaking changes. Refactors internos sem efeito observável, chores de CI e correções tipográficas não entram.
3. **Como.** A entrada descreve o efeito e o porquê, no estilo já adotado no arquivo — não uma lista de arquivos tocados. Emendas desta Constituição registram-se no **Registro de Emendas** abaixo; só atravessam o changelog se alterarem comportamento do produto.

O agente atualiza `CHANGELOG.md` antes de declarar a tarefa concluída.

## Autonomia

Concessão humana ao pipeline CodeSteer. Este documento define **quanto** de autonomia existe; os critérios de auto-aprovação e os hard floors permanecem canônicos na `autonomy-policy` do orquestrador.

- autonomy_default: auto-safe
- autonomy_max: auto-safe

`auto-full` não está habilitado. Pedido do usuário nunca eleva acima de `autonomy_max`. Hard floors (operação irreversível, superfície de segurança, complexidade L/XL, loops esgotados) continuam parando no humano em qualquer nível.

## Governance

- Esta Constituição é o documento de maior relevância normativa no repositório. As regras aqui estabelecidas prevalecem sobre convenções locais ou conteúdo de `.memory-bank/operational-memory.md` quando houver conflito ou ambiguidade.
- Qualquer alteração exige incremento de versão, atualização de `Last Amended` e registro dos princípios alterados.
- Mudanças relevantes do produto são registradas em `CHANGELOG.md` no mesmo conjunto de alterações (ver *Changelog Sempre Atualizado*).

### Registro de Emendas

**2.2.0 (2026-09-05)** — Torna obrigatório manter `CHANGELOG.md` atualizado no mesmo conjunto de alterações. Princípios I–VI inalterados.

| Campo | Mudança |
|---|---|
| — | Novo workflow **Changelog Sempre Atualizado**: entradas sob `[Unreleased]`, Keep a Changelog + SemVer, só comportamento observável. Entrega sem changelog, quando cabível, é incompleta. |
| — | Governance passa a citar `CHANGELOG.md` como registro obrigatório do produto. |

**2.1.0 (2026-09-04)** — Concede autonomia `auto-safe` aos gates HITL. Princípios I–VI inalterados.

| Campo | Mudança |
|---|---|
| — | Nova seção **Autonomia**: `autonomy_default` e `autonomy_max` = `auto-safe`. Hard floors permanecem HITL. |

**2.0.0 (2026-08-30)** — Emenda maior. Remove o teto arquitetural que impedia o Atlas de evoluir além de busca puramente estrutural.

| Princípio | Mudança |
|---|---|
| **I** | *Execução 100% Local e Privada* → *Local-First por Padrão, Fronteira de Dados Declarada*. Camadas de enriquecimento por LLM passam a ser permitidas como opt-in explícito, com ordem de preferência de origem e fronteira inspecionável. O modo offline permanece padrão e suportado. **Mudança incompatível com a versão 1.x.** |
| **II** | *Indexação Sintática via AST* → *Estrutura Antes de Heurística*. Estabelece hierarquia SCIP/LSIF → Tree-sitter e proíbe reimplementar resolução de nomes à mão. |
| **III** | Generalizado de "busca híbrida RRF" para pipeline de recuperação com estágios substituíveis. Adiciona exigência de medição por classe, teto de token como pós-condição, e modelagem de tools pela tarefa do agente. |
| **IV** | Inalterado em substância; estendido explicitamente às chamadas de `sampling`. |
| **V** | Remove a exclusão categórica de watch mode, UI web e reranking. Substitui por invariante real: inicialização instantânea via carregamento preguiçoso. |
| **VI** | **Novo.** *Degradação Explícita*, promovido de prática de facto a princípio. |
| — | **Nova restrição:** *Artefatos de Inspeção Humana*, protegendo `graph.html`. |
| — | **Novo workflow:** *Medição de Ranking* com baseline por classe e flag de ambiente. |

**Version**: 2.2.0 | **Ratified**: 2026-06-05 | **Last Amended**: 2026-09-05
