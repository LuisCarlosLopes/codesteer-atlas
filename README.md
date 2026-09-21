# CodeSteer Atlas

Servidor MCP local para busca semântica em código. Usa Tree-sitter (AST), embeddings locais (`fastembed`/ONNX) e LanceDB. Tudo roda **100% offline** — o código-fonte nunca sai da sua máquina.

### Documentação

**Primeira vez por aqui?** Siga o [guia de primeiros passos](https://luiscarloslopes.github.io/codesteer-atlas/primeiros-passos.html): prepare o Atlas, conecte seu editor e faça a primeira busca, com exemplos para copiar. [Abrir a versão local](docs/primeiros-passos.html) no navegador.

| Recurso | Descrição |
| -------- | ---------- |
| 📖 [Documentação visual](https://luiscarloslopes.github.io/codesteer-atlas/) | Conceitos MCP, busca híbrida e indexação |
| 📘 [Guia didático — Indexação, Grafo e MCP](docs/guia-indexacao-grafo-mcp.md) | Pipeline, diagramas, multi-repo e `graph.html` |

## Funcionalidades

- **Indexação por AST (Tree-sitter)**: chunks por classe/função/método, não por blocos arbitrários de linhas.
- **Busca híbrida**: similaridade vetorial + BM25, fundidas via [RRF](#rrf-e-mrr).
- **Indexação incremental**: só arquivos novos/alterados (hash sha256).
- **Embeddings locais**: `all-MiniLM-L6-v2` (384 dims) via `fastembed`, com lazy loading.
- **Grafo de conhecimento**: `.code-index/graph.json` + visualizador `graph.html` (abre via `file://`).
- **Pacote da tarefa**: `atlas_context` (`edit`/`debug`/`review`/`understand`) e raio de impacto `atlas_graph(mode="affected")`.
- **Watcher opt-in**: `ATLAS_WATCH=1` (extra `[watch]`) reindexa em subprocesso após debounce.
- **SCIP opt-in**: `ATLAS_SCIP=1` produz arestas `calls` (`origin: "scip"`) quando o toolchain está instalado.
- **História local de Git**: sem flag — a indexação publica `history.json`; a busca pode devolver hits `type="commit"`; `atlas_context(intent="debug")` traz `recent_history`.
- **Rationale em código**: `NOTE`/`WHY`, cites `DEC`/`ADR`/`RFC` e wikilinks nos resultados de busca.
- **Multi-linguagem**: Python, JS/TS, Go, Java, C#, Dart, Pascal, VB6, Razor, XML, Markdown e mais.
- **Observabilidade de tokens opt-in**: `ATLAS_OBSERVABILITY=1` mede a resposta de cada tool (chars/bytes/tokens) e aplica teto global em `atlas_search` (as demais já tinham teto de caracteres). Detalhes: [Observabilidade de tokens por consulta](#observabilidade-de-tokens-por-consulta-opcional).
- **Avaliador Jev opt-in**: `ATLAS_RELEVANCE=1` reordena o pool pós-RRF e, no perfil compacto, descarta só irrelevantes de alta confiança. A queda de tokens medida vem da entrega compacta. Detalhes e números: [Avaliador de relevância Jev](#avaliador-de-relevância-jev-opcional).

### Camada semântica opcional

Para gerar propósito por símbolo e sumários hierárquicos, habilite explicitamente
`ATLAS_SEMANTIC=1`. A cadeia usa sampling apenas no caminho MCP síncrono, depois um
endpoint local configurado por `ATLAS_SEMANTIC_LOCAL_URL` e, por último, uma API cujo URL
foi declarado em `ATLAS_SEMANTIC_API_URL`. Sem origem, o índice estrutural continua completo.

APIs OpenAI-compatible, incluindo OpenRouter, usam também `ATLAS_SEMANTIC_MODEL`.
Exemplo: `ATLAS_SEMANTIC_API_URL=https://openrouter.ai/api/v1/chat/completions`,
`ATLAS_SEMANTIC_API_KEY=sk-or-v1-...` e
`ATLAS_SEMANTIC_MODEL=openai/gpt-4.1-mini`. Com o modelo definido, o Atlas envia
`model` + `messages`; sem ele, preserva o payload genérico legado para endpoints customizados.

O índice novo usa formato `2.3.0` e grava `purpose`, `purpose_hash`, `purpose_vector` e o
sidecar `.code-index/semantic.json`; o vetor estrutural não muda. Índices `2.0.x`–`2.2.x`
continuam buscáveis como legados. Para convertê-los, use somente `atlas-index --full` sem
`--paths`; recortes incrementais não fazem migration nem misturam schemas. Consulte
`atlas_status` para auditar `origin`, `egress`, `index` e `last_generation`.

Uma busca degradada inclui `warnings`: `semantic_layer_unavailable` indica camada ligada
sem índice pronto e `semantic_arm_unavailable` indica falha do vetor semântico; em ambos
os casos vector+FTS continuam disponíveis, mas a recuperação semântica está incompleta.

### Observabilidade de tokens por consulta (opcional)

`ATLAS_OBSERVABILITY=1` liga o registro local da string JSON final devolvida por
`atlas_search`, `atlas_context`, `atlas_brief` e `atlas_graph`: chars/bytes e tokens
exatos segundo o **tokenizer padrão do Atlas**, já incluído no pacote.
**Isto mede o texto retornado pela tool, não o prompt inteiro do cliente MCP,
geração do modelo ou faturamento.**

O padrão é o tokenizer de **HuggingFaceTB/SmolLM2-135M**, Apache-2.0, revisão
`93efa2f097d58c2a74874c7e644dbc9b0cee75a2`. O arquivo (~2,1 MB) vem no wheel/sdist,
com licença, origem e SHA-256 fixos em [assets](src/codesteer_atlas/assets/README.md).
Não há download na primeira consulta nem acesso à rede para contar tokens.
O carregamento acontece apenas quando a primeira medição precisar dele.

Para ativar, acrescente ao ambiente do servidor MCP e reinicie-o:

```json
"env": {
  "ATLAS_OBSERVABILITY": "1"
}
```

Desligado (padrão): nenhum arquivo de eventos ou histórico em memória é criado,
e o bloco `observability` fica ausente de `atlas_status`. O contador continua
sendo usado pelos limites de resposta. Ligado: cada tool grava o último evento
em memória (`atlas_status.observability.last_by_tool`) e tenta persistir em
`.code-index/observability/events.jsonl` (rotação em até 3 arquivos de 1 MiB,
~3 MiB no total). Contenção do lock ou falha de E/S descarta a persistência
**daquele** evento (nunca a consulta), incrementa `dropped_events` e avisa uma
vez por transição em stderr.

Para substituir o padrão, configure também `ATLAS_TOKENIZER_PATH` com o caminho
para um `tokenizer.json` compatível com a lib
[`tokenizers`](https://huggingface.co/docs/tokenizers). Variável ausente ou vazia
seleciona o embarcado. Os eventos identificam `tokenizer_source` (`bundled` ou
`custom`), `tokenizer_name`, `tokenizer_revision` e `tokenizer_sha256`. No override,
nome é `custom`, revisão é `null` e o hash identifica o arquivo sem expor o path.
**O tokenizer escolhido não é necessariamente o do cliente MCP**: mantenha a
mesma revisão para comparar medições reproduzíveis.

Arquivo ausente/inválido, recurso embarcado com hash incorreto ou biblioteca
indisponível: degrada para estimativa `ceil(chars/4)`, com
`tokenizer_status: "unavailable"`, e informa o motivo em stderr (`[atlas]`).
Um override inválido não é substituído silenciosamente pelo embarcado.
A falha é memorizada; reinicie o servidor após corrigir ou trocar o arquivo.

O teto de resposta é aplicado **independentemente** da observabilidade.
Search corta resultados inteiros da cauda; context/brief/graph mantêm suas
prioridades de corte. O bloco `budget` declara `mode`, `max_chars`, `max_bytes`,
`max_tokens`, `tokenizer_sha256` e `used_chars`. Com o padrão carregado,
`mode="tokenizer_exact"` e os tetos de tokens passam a valer sem configuração
manual. Isso pode cortar respostas que antes cabiam apenas em chars/bytes.
Se o contador estiver indisponível, o limite passa a chars/bytes:
`mode="byte_bpe_upper_bound"`, `max_tokens=null`, sem garantia de tokens exatos.

### Avaliador de relevância Jev (opcional)

`ATLAS_RELEVANCE` é **False** por padrão (`ausente`/`0`/`false`). Não liga por
presença de chave, modelo ou `ATLAS_SEMANTIC`. Aceite `1`/`true` (sem distinção
de maiúsculas) para enviar a consulta e os candidatos do pool pós-RRF à API
System One do OpenRouter (`POST /api/v1/systemone`, modelo
`~typesafe/jev-latest`). `ATLAS_RERANK=0` continua desligando **toda** reordenação,
inclusive Jev.

A URL default só é derivada de `ATLAS_SEMANTIC_API_URL` quando essa URL é
exatamente o endpoint HTTPS de chat do OpenRouter; a chave
`ATLAS_SEMANTIC_API_KEY` só é reutilizada nesse caso. Para um endpoint próprio,
use `ATLAS_RELEVANCE_API_URL` (apenas `https://openrouter.ai/api/v1/systemone`)
e, se quiser, `ATLAS_RELEVANCE_API_KEY`. `ATLAS_RELEVANCE_MODEL` não herda
`ATLAS_SEMANTIC_MODEL`. Default `~typesafe/jev-latest`; também aceita o alias
sem `~` e pins versionados `typesafe/jev-[0-9]…`.

Jev **reordena** o pool; no perfil compacto também pode excluir só candidatos
com score ≤ 0,25 e confiança ≥ 0,90. Lotes cabem em 24.000 bytes UTF-8 (até
duas chamadas, timeout compartilhado de 3 s). Timeout, HTTP ou schema inválido
caem no reranker local para o lote afetado; baixa confiança isola o candidato
sem invalidar os demais. Reinicie o MCP depois de mudar o `env`.

Cada `atlas_search` emite **uma** linha JSON de custo em stderr (conhecido,
parcialmente conhecido, zero sem chamada, ou desconhecido). Isso **não** é o
tokenizer da observabilidade nem o custo de indexação F4. `ATLAS_OBSERVABILITY=1`
só copia o ledger para o evento JSONL já existente. Logs nunca incluem query,
código, paths ou a chave.

#### Como o Jev atua na resposta

O Jev recebe a consulta e os candidatos do pool pós-RRF e devolve, por
candidato, um score de relevância e uma confiança. Isso acontece antes do
corte `top_k` e do merge com commits. O `score` que a tool devolve continua
sendo o RRF.

1. **Reordena o que já foi recuperado.** Avaliação confiante ocupa as posições
   livres, da maior para a menor. Correspondência exata de símbolo permanece
   na frente, na ordem RRF. Avaliação incerta ou ausente fica onde o ranking
   local a colocou.
2. **Descarta só o irrelevante inequívoco, e só no perfil compacto.** Sai o
   candidato com score ≤ 0,25 e confiança ≥ 0,90. O restante permanece. Se a
   seleção esvaziar o pool, volta o primeiro candidato do ranking local, com
   o aviso `relevance_unconfirmed_fallback`.
3. **Falha devolve o ranking local** daquele lote (timeout, HTTP ou schema
   inválido). `ATLAS_RERANK=0` impede a chamada.

O agente passa a ver primeiro o trecho que o Jev julgou mais útil para a
consulta. `ranking_changed` registra mudança de ordem e
`removed_by_relevance` conta os descartes. `status=success` significa que o
contrato da resposta foi aceito.

#### RRF e MRR

**RRF** (Reciprocal Rank Fusion) junta as duas listas da busca híbrida: a do
vetor (cosseno dos embeddings) e a do BM25 (texto). Os scores dessas listas
estão em escalas diferentes, então a fusão usa só a posição de cada trecho:

```text
RRF(trecho) = soma, em cada lista, de 1 / (60 + posição)
```

A posição começa em 0: o 1º lugar de uma lista vale 1/60, o 2º vale 1/61.
Um trecho em 1º nas duas listas soma as duas contribuições; um que aparece
só numa lista recebe só a dela. O 60 é a constante `RRF_K`. O `score` que
`atlas_search` devolve é esse valor. O Jev reordena o pool depois da fusão.

**MRR** (Mean Reciprocal Rank) mede se o trecho certo apareceu perto do topo.
Para cada consulta do conjunto de teste, a contribuição é `1 / posição` do
primeiro acerto:

| Posição do acerto | Contribuição |
| --- | ---: |
| 1º | 1,00 |
| 2º | 0,50 |
| 5º | 0,20 |
| ausente | 0 |

O MRR é a média dessas contribuições. **1,0** significa que o alvo foi sempre
o primeiro. **0,43**, como nas 28 consultas abaixo, significa que o alvo
costuma aparecer, em geral fora do topo. O **recall@5** registra só se o alvo
entrou nos cinco primeiros; o MRR também penaliza quando ele entra tarde.

#### Evidência: ordem da resposta e tamanho do contexto

A queda de tokens reproduzida no golden set é da **projeção compacta**, com
o Jev desligado. O Jev muda a ordem (e, no compacto, pode tirar um irrelevante
de alta confiança). As duas coisas se medem separadas.

**Contexto, Jev desligado.** 28 consultas, 2.480 chunks, mesma recuperação,
`relevance.requests = 0`. Fonte:
[`tests/eval/context_delivery_20260921.json`](tests/eval/context_delivery_20260921.json).

| Entrega de `atlas_search` | Tokens | MRR | Recall@5 |
| --- | ---: | ---: | ---: |
| Metadados, perfil full | 32.336 | 0,4307 | 0,5714 |
| Metadados, projeção compacta | 21.977 | 0,4307 | 0,5714 |
| Conteúdo incluso, perfil full | 79.784 | 0,4307 | 0,5714 |
| Conteúdo incluso, projeção compacta | 69.403 | 0,4307 | 0,5714 |

A projeção compacta em metadados entregou **32,0% menos tokens** (32.336 →
21.977) com MRR e recall@5 iguais. Deduplicação e seleção ficaram ambas em
22.100 tokens: sem chamada ao Jev, a seleção não cortou candidato além da
deduplicação. O menor contexto é metadados compactos mais `atlas_expand` só
no símbolo que falta.

**Jev ligado, mesma avaliação para todas as variantes.** Modelo
`typesafe/jev-1.13-20260917`, rubrica `jev-relevance-score-v1`. Fonte:
[`tests/eval/context_policy_20260921.md`](tests/eval/context_policy_20260921.md).
Nas 28 consultas e nos 12 cenários, baseline e gate tiveram o mesmo MRR e o
mesmo recall@5:

| Classe | MRR | Recall@5 |
| --- | ---: | ---: |
| Linguagem natural | 0,1292 | 0,375 |
| Símbolo exato | 1,0000 | 1,000 |
| Identificador parcial | 0,4345 | 0,750 |
| Entre arquivos | 0,0000 | 0,000 |

O gate evita a chamada quando a consulta já é um símbolo exato único no pool:
14 de 56 chamadas nas 28 consultas, US$ 0,003150882 contrafactuais (25,5% do
custo dessa rodada). MRR e recall permaneceram os da tabela. Linguagem natural
e relação entre arquivos continuam as classes fracas. A mediana do estágio
remoto foi 1,26 s (p50 1.264,666 ms; p95 1.371,144 ms).

Nessa mesma rodada, o encolhimento adicional veio do lote de expansão. Em
metadados, abrir dois símbolos por vez em vez de cinco passou de 42.268 para
39.260 tokens (−7,12%), com expansões de 16 para 31 e os mesmos 6/12 cenários
completos. Três execuções somaram 106 chamadas e **US$ 0,023391144**, com
todos os custos conhecidos.

A comparação publicada é gate contra Jev sempre ligado, no mesmo ranking.
MRR do Jev contra o rerank local, no mesmo índice, continua por medir.

### Dispensa de Jev para símbolo exato (opcional)

`ATLAS_RELEVANCE_GATE=1` dispensa a chamada remota quando a consulta é um nome
exato e existe apenas uma correspondência de função, método ou classe no pool
recuperado. Não significa unicidade no repositório inteiro. O símbolo exato é
promovido ao primeiro lugar; os demais candidatos mantêm a ordenação local.

O default é **desligado**. Consultas naturais, identificadores parciais, nomes
ambíguos e documentos continuam seguindo a política anterior. A regra existente
para pools com menos de dois candidatos permanece. Os limiares Jev não mudam.
`atlas_status.relevance.gate` declara configuração e versão da política; buscas
dispensadas registram `status=skipped`, `reason=unique_exact_symbol`, nenhuma
chamada remota e `cost_status=not_incurred`. `ATLAS_RERANK=0` continua prevalecendo.

Para avaliação pareada com Jev habilitado, usando as credenciais já configuradas
no ambiente, sem duplicar chamadas remotas entre variantes:

```bash
ATLAS_OBSERVABILITY=1 uv run python scripts/eval_context_policy.py \
  --workspace . --max-cost-usd 0.025 --out /tmp/atlas-context-policy.json
```

Mantenha índice e workspace congelados durante a medição. O harness verifica
os hashes antes de iniciar chamadas pagas e rejeita divergências.

O relatório separa custo real da baseline e custo contrafactual evitável pelo
gate. Compara expansões em lotes de cinco, dois e um; usa os alvos do cenário
somente para verificar cobertura/parar, nunca para reordenar os candidatos.
Interrompe novas consultas ao atingir o teto conhecido ou receber custo
parcialmente conhecido/desconhecido. O teto é verificado após cada consulta.

### Otimização de contexto (opcional)

`ATLAS_CONTEXT_OPTIMIZATION` é **False** por padrão e é independente de
`ATLAS_RELEVANCE`. Com `1`/`true`, `response_profile` default resolve para
`compact` em `atlas_search` e `atlas_context` (ainda dá para forçar `full` por
chamada). Compacto: menos campos por hit, `ref` de expansão, deduplicação
conservadora, orçamento a 70% do teto. `atlas_expand(refs)` recupera até 5
chunks por id sem nova busca. Sem Jev, a compactação é só determinística.
Comece expandindo um ou dois símbolos diretamente ligados à pergunta. Leia o
resultado antes de abrir auxiliares ou continuar páginas; cinco refs é o limite
da chamada, não uma recomendação de lote. Parar quando já existe evidência evita
transferir código adicional. Lotes menores podem aumentar o número de chamadas:
essa troca também deve ser medida, não apenas o tamanho da primeira resposta.


`ref` e `covered_refs` usam IDs curtos do índice atual. A expansão consulta o
manifesto e valida o hash atual e o caminho dentro do workspace. Referências
Base64 antigas continuam aceitas com sua validação de hash. IDs curtos não
representam snapshots históricos após reindexação.

A expansão lê somente o intervalo de linhas do símbolo no arquivo original,
mesmo quando o conteúdo indexado foi truncado. O hash é validado sobre os mesmos
bytes usados na leitura. `content_range` informa offsets em caracteres Unicode
(início inclusivo, fim exclusivo). `content_complete=false` inclui `next_ref`:
passe essa referência a `atlas_expand` se precisar do restante. A última página
traz `content_complete=true`; para reconstituir o símbolo, concatene todas as
partes desde o offset zero. A continuação é vinculada ao hash do arquivo e não
usa cache; se o arquivo mudar, ela fica obsoleta mesmo após reindexação.

Os tetos de bytes e tokens valem para cada resposta inteira, incluindo referências
e metadados. A paginação prefere quebras de linha; linhas muito longas podem ser
divididas por caracteres. Logs de expansão incluem `continuations_returned` e
`content_complete_results` (páginas que chegaram ao fim do símbolo).

A seleção conservadora e a deduplicação acontecem no pool antes do `top_k`.
Correspondências exatas e avaliações incertas são preservadas; se todos forem
rejeitados, retorna o primeiro candidato do ranking local com aviso. Commits
ocupam apenas vagas restantes, seguindo a política existente.

Nos logs, `bytes_recovered` e `bytes_selected` medem conteúdo interno;
`bytes_delivered` mede o JSON final. Os dois primeiros não demonstram economia
serializada. `ranking_changed` registra mudança de ordem e
`confident_evaluations` conta avaliações confiantes. `status=success` significa
processamento válido, não melhoria de qualidade. Custos desconhecidos continuam
explicitamente desconhecidos.

O benchmark usa a mesma montagem do MCP e a mesma recuperação para `full`,
projeção compacta, deduplicação e seleção. A chamada Jev é contabilizada uma vez.
Para comparar as 28 consultas e os cenários com busca mais expansões:

```bash
ATLAS_OBSERVABILITY=1 uv run python scripts/eval_search.py --delivery \
  --tasks tests/eval/task_scenarios.yaml --workspace . --out /tmp/atlas-delivery.json
```

Os cenários expandem resultados em ordem até cobrir a evidência ou esgotar as
referências, seguindo também `next_ref` (limite de 100 chamadas por cenário).
É uma política determinística de avaliação, não uma simulação de um agente.
A evidência paginada só conta quando a cadeia é contígua desde o offset zero.
Conteúdo incompleto ou obsoleto e relações não verificadas impedem classificar
a tarefa como completa. Use `ATLAS_RELEVANCE=0` para medir apenas a
compactação local; o benchmark respeita a configuração do avaliador.

#### Resultado observado com expansão progressiva

Em duas execuções da mesma tarefa no Cursor, o agente passou a abrir dois
símbolos e depois mais um, em vez de cinco de uma vez. A medição controlada
das 28 consultas está em
[Evidência: ordem da resposta e tamanho do contexto](#evidência-ordem-da-resposta-e-tamanho-do-contexto).

| Métrica | Execução anterior | Expansão progressiva |
| --- | ---: | ---: |
| Tokens entregues por todas as ferramentas Atlas | 8.433 | 5.712 |
| Buscas | 5 | 2 |
| Símbolos expandidos | 5 em uma chamada | 2 + 1 em duas chamadas |
| Tokens das expansões | 4.681 | 4.304 |
| Chamadas remotas Jev | 8 | 3 |
| Mediana do tempo das buscas | 2,15 s | 1,04 s |
| Timeouts Jev | 1 | 0 |

A redução observada foi **32,3% dos tokens entregues**, principalmente por menos
operações; as expansões isoladamente reduziram **8,1%**. Os contadores usam o
tokenizador do Atlas e medem respostas das ferramentas, não o consumo total ou
o faturamento do agente. Não é uma comparação controlada nem uma economia
garantida: consultas, candidatos e operações podem variar entre execuções.

A execução custou **US$ 0,000553644** em Jev, com todos os custos
conhecidos e três símbolos entregues completos, sem truncamento ou referências
obsoletas. A anterior teve uma tentativa com custo desconhecido, portanto não
permite calcular a redução percentual do custo total. Nenhuma busca registrou
`unique_exact_symbol`: este teste não valida a dispensa por símbolo exato nem
atribui a ela a economia. Os logs também não comprovam a qualidade da resposta final.

## Começar (3 passos)

Para um percurso guiado e ajuda com erros comuns, veja [Primeiros passos](https://luiscarloslopes.github.io/codesteer-atlas/primeiros-passos.html). Abaixo está a referência rápida de instalação.

Pré-requisitos: Python 3.11–3.13 e [uv](https://github.com/astral-sh/uv) (fornece o `uvx`).

Você **não precisa clonar** este repositório para usar o Atlas. O índice fica em `.code-index/` na **raiz do seu projeto** (adicione essa pasta ao `.gitignore`).

### 1. Conectar o MCP no seu projeto

> **Importante — não instale o plugin em escopo global (user).**
>
> Plugins/MCP globais costumam iniciar o servidor com CWD = `$HOME`, sem a raiz do projeto aberto. Nesse caso o Atlas **não consegue inferir** de forma confiável a pasta `.code-index` do workspace (e pode criar ou achar um índice no lugar errado).
>
> Use sempre uma destas opções:
>
> 1. **Plugin no projeto atual** (escopo *project* ou *local*), ou
> 2. **Configuração manual** via `mcp.json` / `.mcp.json` **na raiz do projeto**.

#### Opção A — Plugin no projeto atual (Claude Code)

```text
/plugin marketplace add LuisCarlosLopes/codesteer-atlas
# ou pasta local: /plugin marketplace add /caminho/para/codesteer-atlas

/plugin install codesteer-atlas
```

Quando o Claude Code pedir o escopo, escolha **Project** (compartilhado no repo) ou **Local** (só neste workspace). **Não escolha User.**

Pela CLI:

```bash
claude plugin install codesteer-atlas --scope project
# ou: --scope local
```

#### Opção B — `mcp.json` manual (recomendado para Cursor, VS Code, Kiro, OpenCode…)

Copie o manifest **para a raiz do seu projeto** (não para a config global do editor) e reinicie o cliente:

| Cliente | Copiar de | Para |
| ------- | --------- | ---- |
| Cursor | [`examples/clients/cursor/mcp.json`](examples/clients/cursor/mcp.json) | `.cursor/mcp.json` |
| GitHub Copilot (VS Code) | [`examples/clients/vscode/mcp.json`](examples/clients/vscode/mcp.json) | `.vscode/mcp.json` |
| Kiro | [`examples/clients/kiro/settings/mcp.json`](examples/clients/kiro/settings/mcp.json) | `.kiro/settings/mcp.json` |
| OpenCode | [`examples/clients/opencode/opencode.json`](examples/clients/opencode/opencode.json) | `opencode.json` |
| Claude Code | [`.mcp.json`](.mcp.json) | `.mcp.json` na raiz do projeto |

Exemplo mínimo (Claude Code / vários clientes com chave `mcpServers`):

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
        "atlas-serve"
      ]
    }
  }
}
```

Detalhes por cliente e modo instalado (`uv tool install`): [`examples/clients/`](examples/clients/) e [CONTRIBUTING.md](CONTRIBUTING.md#configuração-manual-em-outros-clientes).

#### Outros canais (também por projeto)

- **Kiro Power**: Add Custom Power → Import from GitHub → `https://github.com/LuisCarlosLopes/codesteer-atlas.git`, e associe ao workspace atual.
- **Copilot CLI plugin**: prefira instalar no contexto do repositório em que você vai trabalhar; se o índice não for encontrado, use a Opção B (`.vscode/mcp.json` ou equivalente).

```bash
copilot plugin install LuisCarlosLopes/codesteer-atlas
```

### 2. Indexar o projeto

Na raiz do **seu** projeto (não do repositório do Atlas, a menos que seja esse o alvo):

```bash
cd /caminho/para/seu-projeto

# Uma vez: instala atlas-index / atlas-serve no PATH
uv tool install git+https://github.com/LuisCarlosLopes/codesteer-atlas.git

atlas-index --workspace .
```

Sem instalar no PATH (baixa o pacote a cada execução):

```bash
uvx --from git+https://github.com/LuisCarlosLopes/codesteer-atlas.git atlas-index --workspace .
```

Ao terminar: mensagem `Indexação Concluída com Sucesso!` e pasta `.code-index/` com `manifest.json`, `lancedb/`, `graph.json` e `graph.html`.

Atualizar o Atlas depois: `uv tool upgrade codesteer-atlas`.

### 3. Usar

Com o MCP conectado e o índice criado, o agente passa a ter as tools `atlas_*`. Nas próximas vezes:

```bash
atlas-index --workspace .            # incremental (padrão)
atlas-index --workspace . --full     # rebuild completo
atlas-index --workspace . --paths src --paths docs
```

Ou peça ao agente para usar a tool `atlas_index`.

> **Reindex automático:** ao iniciar o `atlas-serve` (abrir/reiniciar o editor), se `.code-index/` já existir, roda uma reindexação incremental em background. A **primeira** indexação (passo 2) continua manual. Log: `.code-index/background_reindex.log`.

## Uso

| Tool | Descrição |
|---|---|
| `atlas_search` | Busca híbrida. Por padrão retorna só metadados; use `include_content=true` ou `Read` nas linhas. Filtros: `repo`, `language`, `path_prefix`. Opt-in: `structural=true` (braço do grafo). Hits de Git vêm como `type="commit"`. |
| `atlas_brief` | Briefing do projeto (identidade, camadas, entrypoints, hubs). Chame primeiro em projeto desconhecido. `level=0` ou `1`. |
| `atlas_context` | Pacote da tarefa (`target` + `intent`: `edit`/`debug`/`review`/`understand`) numa chamada, com teto de tokens. Só `debug` inclui `recent_history`. |
| `atlas_graph` | Grafo: `hubs`, `path`, `explain`, `affected`. |
| `atlas_index` | Indexa/reindexa; regenera `graph.json` / `graph.html`. Suporta `dry_run`. |
| `atlas_status` | Diagnóstico (`is_stale`, `graph_available`, `index_resolution`, `watch`, `semantic`, `resolution_coverage`, e `observability` só com `ATLAS_OBSERVABILITY=1`). |

Recurso somente leitura: `atlas://status`.

Após indexar, abra `.code-index/graph.html` no navegador (`file://`) para inspecionar o grafo.

### Rationale e grafo

Em resultados de código, `atlas_search` pode incluir `rationale_refs` (`DECISAO-002`, `ADR-001`, `[[wikilinks]]`, `# NOTE:` / `# WHY:`).

```text
atlas_graph(mode="hubs", top_n=10)
atlas_graph(mode="path", source="src/app.py", target="dec-002")
atlas_graph(mode="explain", target="AuthService.login")
atlas_graph(mode="affected", target="AuthService.login")
atlas_context(target="AuthService.login", intent="edit")
```

> **Upgrade:** `atlas_graph` / `graph.html` exigem reindex em índices antigos (&lt; `2.1.0`).

## Instruções para agentes de IA (AGENTS.md / CLAUDE.md)

Copie o bloco abaixo para as instruções do seu projeto:

| Cliente / IDE | Arquivo |
|---|---|
| Cursor, Copilot (VS Code), Codex, genérico | [`AGENTS.md`](AGENTS.md) |
| Claude Code | [`CLAUDE.md`](CLAUDE.md) importa [`AGENTS.md`](AGENTS.md) |
| Kiro | regras do Power / instruções do agente |
| GitHub Copilot CLI | instruções do plugin ou regras do projeto |

```markdown
# Busca de código com `codesteer-atlas`

Use Atlas antes de `grep`, `rg`, `find`, glob ou leitura em massa para
entender, planejar ou investigar código e documentos deste projeto.

## Escolha a ferramenta pela tarefa

- Símbolo ou arquivo já conhecido: comece com `atlas_context(target, intent)`
  (`edit`, `debug`, `review` ou `understand`).
- Projeto desconhecido, sem alvo definido: comece com `atlas_brief` uma vez.
- Localizar implementação ou conceito: `atlas_search` com metadados,
  `top_k` baixo e filtros `path_prefix`/`language` quando úteis.
- Ler conteúdo de um resultado compacto: `atlas_expand(refs)`.
- Relações, caminhos e impacto: `atlas_graph`; use `mode="affected"` para impacto.
- Diagnosticar frescor e cobertura: `atlas_status`, somente quando necessário.
- Criar ou atualizar o índice: `atlas_index`, quando o diagnóstico indicar.

## Contexto mínimo suficiente

1. Em `atlas_search` e `atlas_context`, omita `response_profile` ou use `default`
   para respeitar a configuração do operador. Não envie `full` por rotina:
   ele sobrescreve a compactação mesmo quando ela está habilitada.
2. Comece a busca com metadados. No perfil compacto, expanda apenas uma ou duas
   refs diretamente ligadas à pergunta e leia a resposta antes de pedir mais.
3. Cada expansão adicional deve preencher uma lacuna concreta de evidência.
   Não abra todos os hits, auxiliares ou tipos automaticamente. Lotes de três
   a cinco refs só quando a tarefa já exigir comparar esses símbolos.
4. Pare quando houver evidência suficiente. Se `atlas_context` já respondeu à
   necessidade, não repita o conteúdo. Sem refs, leia somente as linhas úteis
   com `Read` ou use `include_content=true` numa busca restrita.
5. Siga `next_ref` apenas quando precisar do restante. Conteúdo paginado só
   representa o símbolo completo após reunir todas as partes desde o offset zero.
   Referência obsoleta exige localizar novamente o símbolo e verificar o índice.
6. Use `full` somente se solicitado ou se precisar de campos ausentes no compacto.

## Evidência e limites

- Respeite os avisos de truncamento, baixa confiança e cobertura incompleta.
  `calls_unavailable` ou ausência de resolução não significam ausência de dependências.
- Relevância Jev e dispensa por símbolo exato são decisões do servidor conforme
  as flags do operador; não altere configurações para forçar uma busca.
- `status=success` e mudança de ranking não comprovam qualidade da evidência.
- Não chame `atlas_status` antes de toda busca. Se houver erro de índice ou
  evidência de desatualização, diagnostique e reindexe quando necessário.

## Fallback local

Use ferramentas locais se o MCP estiver indisponível ou a cobertura for
insuficiente, explicando a limitação. Atlas não substitui edição, diff, Git,
CI, testes ou instalação de dependências. Um caminho já conhecido permite
leitura pontual; para entender seu contexto e relações, prefira `atlas_context`.
```

## Como funciona

O Atlas divide cada arquivo em `CodeChunk`s no nível de símbolo via Tree-sitter, gera embeddings locais e indexa em LanceDB (vetorial + BM25, fundidos por [RRF](#rrf-e-mrr)):

```
src/auth/service.py
  ├── class AuthService          (linhas 10–45)
  ├── AuthService.login          (linhas 20–35)
  └── AuthService.logout         (linhas 37–44)
```

Detalhes do pipeline: [CONTRIBUTING.md](CONTRIBUTING.md#pipeline-de-indexação-detalhado).

### Excluindo arquivos com `.atlasignore`

Na raiz do workspace (sintaxe igual à do `.gitignore`):

```gitignore
*.log
fixtures/
/dist
**/*.generated.py
!important.log
```

É um filtro **adicional** — `.git`, `node_modules`, `.venv`, `__pycache__` e `.code-index` continuam sempre ignorados.

## Onde fica o `.code-index`?

Ordem de resolução:

1. `--index-dir` (CLI)
2. `ATLAS_INDEX_DIR` (env)
3. Busca ascendente a partir do CWD
4. Busca a partir da raiz do editor (`CLAUDE_PROJECT_DIR`, `WORKSPACE_FOLDER_PATHS`)
5. Fallback `.code-index` relativo à raiz conhecida (ou ao CWD)

Com MCP ligado **ao projeto** (plugin project/local ou `mcp.json` na raiz), o item 3 ou 4 costuma bastar após `atlas-index --workspace .`.

Se o servidor nascer com CWD errado (caso típico de instalação **global**), o Atlas tenta recuperar via MCP `roots/list` quando o cliente suporta. Mesmo assim, **prefira instalação por projeto** — é o caminho estável.

Para forçar um caminho explícito no `mcp.json` do projeto:

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
        "atlas-serve"
      ],
      "env": {
        "ATLAS_INDEX_DIR": "${workspaceFolder}/.code-index"
      }
    }
  }
}
```

> No Cursor, `${workspaceFolder}` é a forma mais segura de amarrar o índice ao projeto aberto. Veja [CONTRIBUTING.md — Cursor](CONTRIBUTING.md#cursor).

Diagnóstico: `atlas_status` → `index_resolution`.

## Configuração e variáveis de ambiente

Todas as flags abaixo são **opt-in ou de override**. Sem elas, o Atlas indexa, busca e serve o grafo 100% local — igual à `main` 2.1.x, mais as tools F1.

| Variável | Default | Efeito |
|---|---|---|
| `ATLAS_INDEX_DIR` | discovery / fallback `.code-index` | Caminho explícito do índice (prioridade 2 da resolução). |
| `ATLAS_RERANK` | ligado | `0` desliga **toda** reordenação pós-RRF (lexical e cross-encoder). |
| `ATLAS_RERANK_MODEL` | ausente | Presente → cross-encoder ONNX (o valor é o slug do modelo; default `Xenova/ms-marco-MiniLM-L-6-v2`). Falha de carga: `warnings: cross_encoder_unavailable`. |
| `ATLAS_WATCH` | desligado | `1` observa o workspace e dispara reindex incremental em subprocesso após 2 s. Extra: `pip install "codesteer-atlas[watch]"` (`watchdog`). Sem o extra: `watch: "unavailable"`. |
| `ATLAS_SCIP` | desligado | `1` invoca o indexador SCIP da linguagem (`scip-python`, `scip-typescript`, `scip-go`, `rust-analyzer`) e produz arestas `calls`. Sem toolchain: `scip_status: "toolchain_missing"`. |
| `ATLAS_SEMANTIC` | desligado | `1` liga a camada de propósito por símbolo. Sem origem configurada, o índice estrutural continua completo. Detalhes: [Camada semântica opcional](#camada-semântica-opcional). |
| `ATLAS_SEMANTIC_LOCAL_URL` | ausente | Endpoint local (segunda origem, depois do sampling MCP). |
| `ATLAS_SEMANTIC_API_URL` | ausente | URL explícita de API (terceira origem). Sem host default. |
| `ATLAS_SEMANTIC_API_KEY` | ausente | Só no header da API. |
| `ATLAS_SEMANTIC_MODEL` | ausente | Contrato OpenAI-compatible (`model` + `messages`). Sem ele, payload genérico legado. |
| `ATLAS_OBSERVABILITY` | desligado | `1` grava eventos de medição de resposta (chars/bytes/tokens) em memória + `.code-index/observability/events.jsonl` e expõe `atlas_status.observability`. Sem ele, nada é criado. Detalhes: [Observabilidade de tokens por consulta](#observabilidade-de-tokens-por-consulta-opcional). |
| `ATLAS_RELEVANCE` | desligado | `1`/`true` avalia relevância com Jev via OpenRouter System One antes do `top_k`. Default False. `ATLAS_RERANK=0` vence. Detalhes: [Avaliador de relevância Jev](#avaliador-de-relevância-jev-opcional). |
| `ATLAS_RELEVANCE_API_URL` | derivado só de OpenRouter chat | Endpoint HTTPS System One. Sem host implícito genérico. |
| `ATLAS_RELEVANCE_API_KEY` | reuso condicional | Se ausente, reutiliza `ATLAS_SEMANTIC_API_KEY` somente quando a URL semântica é OpenRouter HTTPS. |
| `ATLAS_RELEVANCE_MODEL` | `~typesafe/jev-latest` | Não herda `ATLAS_SEMANTIC_MODEL`. Alias OpenRouter ou pin `typesafe/jev-[0-9]…`. |
| `ATLAS_RELEVANCE_GATE` | desligado | `1`/`true` dispensa Jev para símbolo exato único no pool; preserva promoção local do exato. |
| `ATLAS_CONTEXT_OPTIMIZATION` | desligado | `1`/`true` faz `response_profile=default` resolver para compacto em search/context. Independente do Jev. Detalhes: [Otimização de contexto](#otimização-de-contexto-opcional). |
| `ATLAS_TOKENIZER_PATH` | ausente | Caminho de um `tokenizer.json` local (lib `tokenizers`) para contagem EXATA de tokens e teto de tokens no orçamento de resposta. Independente de `ATLAS_OBSERVABILITY`. Sem ele (ou inválido), estimativa `ceil(chars/4)` identificada como tal — `max_tokens` fica `null` em qualquer SO; isso é esperado, não um bug. |

História de Git **não tem variável de ambiente**. A janela é teto interno (até 100 commits por arquivo e 24 meses). Extra opcional: `codesteer-atlas[watch]`.

No `mcp.json` do projeto:

```json
{
  "mcpServers": {
    "codesteer-atlas": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git",
        "atlas-serve"
      ],
      "env": {
        "ATLAS_INDEX_DIR": "${workspaceFolder}/.code-index",
        "ATLAS_WATCH": "1"
      }
    }
  }
}
```

## Contribuindo

Clonar o repo, testes, lint e configuração avançada: [CONTRIBUTING.md](CONTRIBUTING.md) e [AGENTS.md](AGENTS.md).

## Licença

Veja [LICENSE](LICENSE).
