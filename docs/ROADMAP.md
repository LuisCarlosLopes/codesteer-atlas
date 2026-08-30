# Roadmap — CodeSteer Atlas

> Reescrito sobre a **Constitution 2.0.0** (2026-08-30), que removeu o teto arquitetural
> da versão 1.x. Substitui integralmente a versão anterior deste documento.

**Baseline verificado:** `index_version` 2.1.0 · pacote 2.1.1 · 5 tools MCP · 40 extensões
em `SUPPORTED_EXTENSIONS` · 5 tipos de aresta no grafo · rerank lexical com **MRR 0.605**
no golden set de 28 queries em 4 classes.

---

## A premissa

Atlas não compete com outros motores de busca semântica. Compete com **o agente usando
`grep` e abrindo arquivos por conta própria**. A condição de vitória é chegar ao código
certo em menos tokens e menos turnos do que o agente chegaria sozinho.

Medido contra isso, o Atlas hoje responde bem *onde está*, mal *como se conecta*, e quase
nada de *por que é assim*.

E há uma assimetria que praticamente nenhum servidor MCP explora: **o cliente do Atlas é um
LLM.** Quase todo servidor MCP é projetado como se o cliente fosse um programa burro. Três
dos cinco itens deste roadmap saem dessa observação.

---

## O que mudou em relação à v1 deste roadmap

A v1 foi escrita sob a Constitution 1.x. Quatro itens dela perderam a razão de existir —
não por estarem errados, mas porque havia caminho melhor atrás da restrição.

| Item v1 | Destino | Motivo |
|---|---|---|
| **B** — arestas `calls` por match de label | **Substituído** por ingestão SCIP (§3.2) | Resolver chamadas cross-file com alias de import e match de label é reimplementar um front-end de compilador, mal. Os toolchains já resolveram isso corretamente. |
| **G** — `graph_diff` no `atlas_status` | **Eliminado** pelo watcher (§3.1) | Diagnosticar staleness perde sentido quando staleness deixa de existir. |
| **H** — hooks de git | **Eliminado** pelo watcher (§3.1) | Mesma razão; o watcher cobre o caso do `git pull` e mais. |
| **J** — feedback no RRF | **Rebaixado** | O cross-encoder (§2.1) entrega mais ganho, antes, sem risco de loop de reforço. Reavaliar depois de F2. |
| **A** — imports multi-linguagem | **Mantido, rebaixado** a fallback (§3.3) | Continua sendo o caminho onde não há toolchain SCIP. |
| **C, D, E** — affected, teto, denylist | **Mantidos, repriorizados** para F1 | Deixam de ser melhorias do `atlas_graph` e viram infraestrutura do `atlas_context`. |
| — | **`graph.html` preservado** (§1.5) | Proposta de remoção rejeitada. Ver Constitution 2.0.0, *Artefatos de Inspeção Humana*. |

---

## Fases

| Fase | Tema | Dependências | Aposta |
|---|---|---|---|
| **F1** | Superfície orientada à tarefa | nenhuma | baixa — só montagem sobre o que existe |
| **F2** | Recuperação medida | nenhuma | baixa — mensurável no golden set |
| **F3** | O índice reflete a realidade | nenhuma | média — SCIP depende de toolchain |
| **F4** | Camada semântica | F2 (para poder medir) | **alta** |
| **F5** | Arqueologia de git | F1 | média |

F1 e F2 são independentes entre si e podem correr em paralelo. F3 é independente de ambas.
F4 exige F2 porque é a aposta de maior variância e não deve entrar sem medição confiável
para julgá-la.

---

# F1 · Superfície orientada à tarefa

> Melhor retorno por linha escrita de todo o roadmap. Nenhuma capacidade nova —
> apenas mover a composição do agente para o servidor.

## 1.1 `atlas_context` — a tool que responde à tarefa

### Problema

`atlas_search`, `atlas_graph`, `atlas_brief` são **primitivas do índice**, não da tarefa. Um
agente que vai editar `StorageBackend.search_hybrid` precisa de: o símbolo, quem o chama,
o que ele chama, os testes que o cobrem, o rationale ancorado, os ADRs citados. Hoje isso
são cinco chamadas, cinco round-trips, e três contextos parciais que o agente reconstrói
sozinho — mal e caro.

### Proposta

```python
atlas_context(target="StorageBackend.search_hybrid", intent="edit")
```

Um pacote montado, com teto de token, contendo:

| Intent | Composição |
|---|---|
| `edit` | símbolo + chamadores (com call site) + chamadas de saída + testes que o cobrem + rationale |
| `debug` | símbolo + cadeia de chamada até os entrypoints + tratamento de erro no caminho + histórico recente |
| `review` | diff-alvo + raio de impacto + testes afetados + ADRs citados |
| `understand` | símbolo + camada a que pertence + vizinhos por grau + o que o brief diz da camada |

### Como implementar

Nenhuma capacidade nova é necessária — é orquestração de peças existentes mais as três
seguintes desta fase. A montagem vive em um módulo novo, `context.py`, com uma função por
intent e um orçamento compartilhado.

**Alocação de orçamento, não concatenação.** O erro fácil aqui é montar tudo e truncar no
fim, o que corta justamente a última seção. Cada seção recebe uma cota
(`CONTEXT_BUDGET_BY_SECTION`), preenche até ela, e devolve o que sobra ao pool para as
seções seguintes. Uma seção vazia não desperdiça sua cota.

**Descoberta de testes** por convenção sobre o `manifest.files`: `test_<módulo>.py`,
`<módulo>_test.go`, `<Classe>Test.java`, mais qualquer arquivo cujo chunk referencie o
símbolo-alvo. Reportar como `inferred` quando vier de convenção.

### Testes

`tests/test_context.py`: cada intent devolve as seções esperadas; o pacote nunca excede o
teto; uma seção vazia devolve sua cota ao pool; alvo inexistente levanta erro acionável.

**Esforço:** médio · **Impacto:** alto · **Risco:** baixo

---

## 1.2 Teto de token como pós-condição

### Problema

`graph.py:635` — `explain()` itera a adjacência inteira e devolve **todos** os vizinhos, sem
corte. Em um hub, isso despeja centenas de nós no contexto. Com o `atlas_context` compondo
sobre `explain`, um corte ausente deixa de ser incômodo de diagnóstico e vira bug do caminho
principal.

A Constitution 2.0.0, Princípio III, agora exige teto **como pós-condição no serializador**.

### Proposta

```python
# config.py
GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND = 12
GRAPH_AFFECTED_MAX_RESULTS = 40
GRAPH_RESPONSE_MAX_CHARS = 6000
CONTEXT_RESPONSE_MAX_CHARS = 12000
```

### Como implementar

Ordenar vizinhos por `degree` **decrescente** antes de cortar — o mais central é o mais
informativo — e devolver `truncated: {"symbol": 34, "file": 2}` por kind. Aplicar o teto
final no serializador, espelhando o que `render_brief` já faz.

> **Princípio VI.** O corte é sempre reportado. Um corte silencioso produz uma conclusão
> errada com aparência de completa — que é pior que uma resposta grande.

**Esforço:** baixo · **Impacto:** alto · **Risco:** nenhum

---

## 1.3 Denylist de ruído no ranking de hubs

### Problema

`graph.py:617` — `hubs()` lê `metrics.top_hubs`, calculado por **grau bruto**. Sem filtro, o
topo de qualquer repositório Python é dominado por `Path`, `Optional`, `logger`, `utils`,
`json`.

Isso deixa de ser cosmético quando o `atlas_context` e o braço estrutural (§2.2) passam a
**caminhar** o grafo: expandir através de um hub de ruído traz 200 vizinhos irrelevantes por
hop. É o caso de explosão combinatória.

### Como implementar

Há **dois** rankings independentes, e ambos precisam do filtro:

1. `_finalize_graph` em `graph.py`, que monta `metrics.top_hubs`.
2. `brief.py::_compute_hubs`, que **deliberadamente não reusa** `metrics.top_hubs` — porque
   ele é capado antes de qualquer filtro — e hoje aplica só um filtro por `kind`.

Extrair um predicado compartilhado `is_noise_hub(node) -> bool` e chamá-lo dos dois lados.
Aplicar de um lado só faz `atlas_brief` e `atlas_graph hubs` divergirem entre si — pior que
não filtrar.

Manter os nós no grafo; excluí-los apenas do **ranking** e da **expansão**.

**Esforço:** baixo · **Impacto:** alto · **Risco:** baixo

---

## 1.4 Raio de impacto (`affected`)

### Problema

*"O que quebra se eu mudar isto?"* não tem resposta no Atlas. É a pergunta que o intent
`edit` do `atlas_context` precisa responder, e a base do intent `review`.

### Como implementar

`_build_reverse_adjacency` construída no mesmo passe de `_build_adjacency` e cacheada no
mesmo `_GRAPH_CACHE`, que já é invalidado por `mtime_ns` + `size`. BFS reversa com:

- `relations` default `{calls, imports}`. `contains` fica de fora, senão todo símbolo do
  arquivo entra como afetado.
- **Semeadura pelos membros:** se o alvo é uma classe, semear também seus filhos `sym:` via
  `contains` — uma hop, sem reportar como hit. Sem isso, um chamador que aponta para o
  método não é alcançável a partir da classe.
- Retorno com `via_location`: a linha do call site **no arquivo do dependente**. É a
  diferença entre "este arquivo é afetado" e "clique aqui".

Exposto como `atlas_graph mode="affected"` e consumido internamente pelo `atlas_context`.

**Esforço:** médio · **Impacto:** alto · **Risco:** baixo

---

## 1.5 `graph.html` — preservado e promovido

### Decisão

A remoção do `viewer.py` foi proposta e **rejeitada**. O Atlas é consumido por agentes, mas
é operado, auditado e depurado por pessoas — e o grafo é justamente o artefato em que um
erro estrutural é óbvio para um humano e invisível para um agente. Registrado na
Constitution 2.0.0 em *Artefatos de Inspeção Humana*.

### O que muda

O viewer deixa de ser um artefato estático e passa a renderizar o que as fases seguintes
produzem — é a ferramenta de auditoria de cada uma delas:

| Vem de | O que o viewer passa a mostrar |
|---|---|
| §1.3 | hubs de ruído esmaecidos, distinguíveis dos hubs reais |
| §2.2 | os nós que o braço estrutural promoveu em uma query |
| §3.2 | **origem da aresta**: SCIP (sólida) × Tree-sitter (tracejada) |
| §3.3 | linguagens sem resolver, marcadas visualmente |
| §4 | símbolos com descrição semântica gerada, e a origem dela |

Filtro por tipo de aresta e por confiança. Continua autocontido e offline — `file://`, sem
CDN, com o `force-graph` vendorizado.

> Sem isso, a auditoria das camadas novas seria ler JSON. O viewer é o que torna o degrau de
> qualidade do SCIP e o ruído da camada semântica **visíveis em cinco segundos**.

**Esforço:** baixo por fase · **Impacto:** médio · **Risco:** nenhum

---

# F2 · Recuperação medida

> Nada aqui entra sem passar pelo golden set, por classe.
> `uv run python scripts/eval_search.py --baseline tests/eval/baseline.json`

## 2.1 Cross-encoder no rerank

### Problema

`ranking.py` é heurística lexical — casamento em nome de símbolo, proximidade de termo,
bônus de frase. Bem construída, medida, **MRR 0.605**. É um teto bom para o que ela é: as
features são independentes entre si e cegas ao significado conjunto de query e documento.

### Proposta

Cross-encoder ONNX (~30M parâmetros) reordenando o pool de 50 candidatos com atenção
conjunta query×documento. Roda local, cabe exatamente no padrão que o `fastembed` já
estabeleceu, e é o passo seguinte padrão em recuperação.

### Como implementar

Novo módulo `reranker.py` com o mesmo desenho do `embeddings.py`: singleton, carregamento
**preguiçoso** na primeira chamada (Princípio V — o startup não pode atrasar). O
`ranking.py` atual permanece como fallback quando o modelo não está disponível, e a escolha
é reportada em `SearchOutcome.warnings` (Princípio VI).

Entra atrás de `ATLAS_RERANK_MODEL`, do mesmo jeito que `ATLAS_RERANK` entrou. Promoção a
padrão só depois de ganho comprovado **nas quatro classes**.

### Critério de aceite

MRR total acima de 0.605 **e** nenhuma classe abaixo da baseline. Regressão em símbolo
exato bloqueia — é metade do golden set.

**Esforço:** médio · **Impacto:** alto · **Risco:** baixo (mensurável e reversível)

---

## 2.2 Braço estrutural no RRF

### Problema

A busca é chunk-level e sem memória de estrutura. Se três dos dez primeiros resultados estão
na mesma vizinhança do grafo, aquela vizinhança é provavelmente a resposta — e um quarto
chunk dela que ficou em 30º deveria subir. Hoje não sobe.

### Proposta

Um terceiro braço na fusão. `_fuse` em `storage.py:361` **já é genérico sobre `arm`**:

```python
_fuse(vector_results, "vector")
_fuse(text_results,   "fts")
_fuse(graph_results,  "graph")   # ← uma linha
```

E `SearchResult.match_arms` já é o lugar onde o consenso entre braços se expõe ao chamador.

### Como implementar

**A junção é gratuita.** O id de nó é `sym:{file_path}#{scope_name}` e `SearchResult` carrega
`file_path` + `scope_name`. Reconstrução determinística — sem coluna nova, sem índice novo.
Markdown usa o prefixo `sec:`; tratar ambos.

O braço produz sua lista ranqueada por **spreading activation**: a partir dos top-N do RRF
inicial, ativar vizinhos com peso decrescente por hop, ordenar. Roda sobre
`CANDIDATES_LIMIT = 50`, então o custo é desprezível.

### O risco que domina esta decisão

Duas das quatro classes do golden set são lookup de símbolo exato — `search_hybrid`,
`EmbeddingEng`, `_build_where_clause`. Nelas a resposta certa é **um** chunk, e o braço
estrutural pode **rebaixar o acerto exato** ao promover seus vizinhos.

É exatamente o modo de falha que o comentário do `golden_queries.yaml` antecipa. Portanto:
**opt-in por chamada ou condicionado à classe de query, nunca default-on.**

Depende de §1.3: sem a denylist, a ativação vaza por `Optional` e `logger`.

**Esforço:** médio · **Impacto:** médio-alto · **Risco:** médio

---

# F3 · O índice reflete a realidade

## 3.1 Índice sempre fresco

### Problema

Um índice velho é o modo de falha mais perigoso do Atlas: ele responde com confiança e está
errado. `BACKGROUND_REINDEX_MIN_INTERVAL_S` depende de o servidor estar rodando e ser
acionado; um `git pull` fora de uma sessão deixa tudo velho até a próxima chamada.

### Proposta

Watcher com debounce sobre o workspace, reindexando incrementalmente o que mudou. A
Constitution 2.0.0 removeu a exclusão categórica de watch mode; o que permanece é a
exigência de não atrasar o startup.

### Como implementar

Novo módulo `watcher.py` sobre `watchdog`, importado **preguiçosamente** e ativado por flag
— nunca no caminho de inicialização do servidor. Debounce de ~2s, respeitando
`load_atlasignore_spec` e `IGNORE_DIRS`. Reusa `locking.py`, que já coordena reindexações
concorrentes.

**Elimina os itens G e H da v1.** Não há mais o que diagnosticar sobre staleness quando ela
deixa de existir; `is_stale` permanece como rede de segurança para quando o watcher está
desligado.

**Esforço:** médio · **Impacto:** alto · **Risco:** baixo

---

## 3.2 Grafo de chamadas correto via SCIP/LSIF

> **Este item substitui o item B da v1**, e é o degrau de qualidade mais alto do roadmap.

### Problema

Tree-sitter dá **sintaxe**, não semântica. Ele não sabe que `self.storage.search_hybrid(...)`
é `StorageBackend.search_hybrid` — isso exige inferência de tipo. Resolver chamadas
cross-file com match de label e alias de import, como a v1 propunha, é reimplementar um
front-end de compilador e conviver para sempre com o caso ambíguo.

`scip-python`, `scip-typescript`, `rust-analyzer` e `gopls` **já resolveram isso
corretamente**.

A Constitution 2.0.0, Princípio II, agora proíbe explicitamente a reimplementação manual
quando um índice do toolchain está disponível.

### Como implementar

Novo módulo `scip_ingest.py`:

1. **Detectar** o toolchain disponível por linguagem presente no manifesto.
2. **Invocar** o indexador (`scip-python index`, `scip-typescript index`, …) como subprocesso,
   com timeout e saída para `stderr`.
3. **Parsear** o protobuf SCIP em ocorrências de definição e referência.
4. **Casar** os símbolos SCIP com os nós `sym:` existentes por `(file_path, linha)` — o
   chunker já registra `start_line`/`end_line`, então o casamento é por contenção de
   intervalo.
5. **Emitir** arestas `calls` com `origin: "scip"`.

Tree-sitter permanece como fallback com `origin: "treesitter"`, e a origem é registrada
**por aresta** — o consumidor sabe o que está pisando (Princípios II e VI).

### O contra, declarado

Exige toolchain por linguagem instalado, e nem sempre está. Por isso é **tier, não
substituição**. `atlas_status` reporta quais linguagens têm resolução semântica e quais
caíram no fallback.

**Esforço:** alto · **Impacto:** alto · **Risco:** médio (dependência externa)

---

## 3.3 Imports multi-linguagem (fallback)

### Problema

`chunker.py:433` retorna `[]` para qualquer linguagem fora de `{python, javascript,
typescript}`, e `graph.py:328` só resolve essas duas famílias. Um repositório Go, Java ou
C# é indexado e buscável, mas seu `graph.json` tem **zero arestas `imports`**.

### Como implementar

Rebaixado de item central da v1 a **fallback para onde o SCIP não chega**, mas ainda
necessário: é o que sustenta os repositórios sem toolchain.

Tabela de despacho no chunker (`_IMPORT_NODE_KINDS`, ~10 linhas por linguagem, reusando
`_collect_nodes_by_kind` e `_decode_node`) e registry de resolvers em `graph.py`
(`_IMPORT_RESOLVERS`, assinatura uniforme por sufixo). Estender `infer_package_roots` para
detectar raiz por manifesto: `go.mod`, `pom.xml`, `*.csproj`, `Cargo.toml`.

Prioridade: Go, Java, C# → Rust, Kotlin → PHP, Ruby, Swift, Scala, Elixir.

### Cobertura exposta

```json
"resolution_coverage": {
  "scip": ["python", "typescript"],
  "treesitter": ["go", "java"],
  "none": ["kotlin", "php"],
  "files_unresolved": 34
}
```

Um teste de regressão falha se `SUPPORTED_EXTENSIONS` ganhar uma linguagem sem entrada
correspondente na tabela de cobertura.

**Esforço:** médio-alto · **Impacto:** médio · **Risco:** baixo

---

# F4 · Camada semântica

> A aposta de maior variância do roadmap. Só entra depois de F2, porque exige medição
> confiável para ser julgada.

## 4.1 Descrição de símbolo gerada por LLM

### O problema real

O gap de recuperação em código não é de ranking, é de **vocabulário**. A query
`"impedir que duas reindexações rodem ao mesmo tempo"` precisa casar com código que diz
`filelock`, `acquire`, `REINDEX_LOCK_FILENAME`. Nem embedding nem BM25 fecham isso de forma
confiável, porque a ponte não existe no texto.

### Proposta

Gerar o lado em linguagem natural **na indexação**: por símbolo, um propósito de uma linha,
os invariantes que mantém, os efeitos colaterais. Embeddar isso como **segundo vetor**, ao
lado do código.

### De onde vem o LLM

A ordem é normativa (Constitution 2.0.0, Princípio I):

1. **`sampling` do cliente MCP** — o servidor pede completions ao modelo do próprio cliente.
   Sem API key, sem custo além da assinatura que o usuário já tem, e **sem ampliar nenhuma
   fronteira de dados**: o código já está no contexto do agente que fez a chamada.
2. **Modelo local** — Ollama, llama.cpp, qualquer endpoint compatível.
3. **API externa** — último recurso, explicitamente configurado.

Suporte a `sampling` varia entre clientes MCP. A cadeia de fallback é requisito, não
conveniência.

### A mitigação é arquitetural, não de prompt

Descrição alucinada envenena a recuperação. A defesa não é escrever um prompt melhor:

> **A descrição nunca substitui o embedding do código — só adiciona um braço.** Um resultado
> que só o braço semântico recuperou já chega ao chamador sem consenso em `match_arms`. O
> sinal de desconfiança já está montado.

### Custo

Uma chamada por símbolo novo ou alterado, cacheada por hash de conteúdo — que o incremental
já calcula. Um repositório de 5 mil símbolos paga uma vez; depois, só o diff.

**Esforço:** alto · **Impacto:** alto · **Risco:** alto

---

## 4.2 Sumário hierárquico por camada

Com 4.1 no lugar, sumarizar de baixo para cima: símbolo → arquivo → camada. Alimenta o
`atlas_brief` e o intent `understand` do `atlas_context`, e responde a perguntas globais que
nenhum chunk isolado responde.

Fica atrás de 4.1 e herda a mesma cadeia de origem e a mesma política de cache.

**Esforço:** médio · **Impacto:** médio · **Risco:** médio

---

# F5 · Arqueologia de git

## 5.1 História como fonte de recuperação

### Problema

*"Por que isto está assim?"* é a pergunta mais cara de responder em código alheio, e a única
fonte é a história — mensagem de commit, corpo de PR, o commit que reverteu algo. O Atlas
hoje usa git apenas para ler o SHA do HEAD.

### Proposta

Indexar mensagens de commit como chunks recuperáveis, **ancoradas nos símbolos que
tocaram**. O intent `debug` do `atlas_context` passa a devolver: *"esta função foi reescrita
três vezes; a última reverteu uma otimização que quebrou X"*.

### Como implementar

`git log --follow --numstat` por arquivo, cruzando os hunks alterados com os intervalos de
linha dos chunks. Commits viram nós `commit:` com arestas `touches` para os símbolos.
Limitado por janela (últimos N commits ou M meses) para não inflar o índice com história
antiga irrelevante.

Reconhecer commits de revert e marcá-los — um revert é o sinal de rationale mais denso que
existe em um repositório.

### Por que isto é a diferenciação

Praticamente nenhuma ferramenta de busca de código expõe história como contexto recuperável.
É rationale que **existe**, que ninguém indexa, e que responde exatamente a classe de
pergunta em que um agente mais erra: por que o código não é do jeito óbvio.

**Esforço:** médio-alto · **Impacto:** alto · **Risco:** médio

---

# Ordem de execução

```
F1  atlas_context (1.1)  ←── 1.2 teto · 1.3 denylist · 1.4 affected
    1.5 graph.html evolui junto de cada fase

F2  2.1 cross-encoder        ─┐ independentes entre si
    2.2 braço estrutural     ─┘ 2.2 depende de 1.3

F3  3.1 watcher              ─── independente, pode antecipar
    3.2 SCIP  ──►  3.3 imports como fallback

F4  4.1 descrição semântica  ──►  4.2 sumário hierárquico
    exige F2 para ser mensurável

F5  5.1 arqueologia de git
    exige F1 (entra como seção do atlas_context)
```

**Comece por F1.** Não depende de nada, não adiciona dependência, e é a maior mudança
percebida por turno de agente.

**F3.1 (watcher) pode ser antecipado a qualquer momento** — é isolado, barato, e remove a
classe de bug mais perigosa do sistema.

---

# Impacto no versionamento do índice

| Fase | Exige rechunk | Ação |
|---|---|---|
| F1 | não | deriva de `graph.json`, reconstruído por `atlas_index` |
| F2.1 | não | rerank é query-time |
| F2.2 | não | junção chunk↔nó é reconstruída em memória |
| F3.1 | não | — |
| F3.2 | sim | `CURRENT_INDEX_VERSION` → `2.2.0` (arestas com `origin`) |
| F3.3 | sim | mesma bump de 3.2 |
| F4 | sim | segundo vetor por chunk → `2.3.0` |
| F5 | sim | nós `commit:` → `2.3.0` (entregar junto de F4 se possível) |

`MIN_INDEX_VERSION` permanece em `2.0.0`: a busca continua funcionando em índices anteriores.
O que **não** pode acontecer é um índice antigo se apresentar como se tivesse resolução
semântica — daí `resolution_coverage` (§3.3) ser requisito, e não enfeite.

---

# O que continua fora de escopo

- **Sumarização de comunidade estilo GraphRAG da Microsoft.** O cliente MCP já é o LLM;
  pré-gerar prosa que o agente geraria melhor é custo sem retorno.
- **Índice hierárquico recursivo (RAPTOR).** O `atlas_brief` já cobre a orientação global
  com teto de token, e F4.2 cobre o resto por um caminho mais barato.
- **Grafo cross-repo global.** Reavaliar depois de F3.
- **Dashboard de PRs e triagem por IA.** Fora do domínio.
