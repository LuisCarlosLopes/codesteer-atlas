# PRD: CodeSteer Atlas — v1.0

> **Status:** Draft (retrospectivo — engenharia reversa do vault Obsidian)
> **Tipo:** Produto Novo (Greenfield)
> **Autor:** codesteer-prd-writer v1.0 | **Data:** 2026-06-13
> **Confiança:** 82/100
> **Fonte:** `docs/vault/` + índice Atlas (745 chunks, 2026-06-13)

---

## 1. VISÃO DO PRODUTO

**Tagline:**
> Busca inteligente no seu código, 100% na sua máquina — para agentes de IA encontrarem o que importa sem ler o repositório inteiro.

**Visão de longo prazo (12–24 meses):**
Tornar-se a camada padrão de descoberta de contexto para agentes de codificação em qualquer editor ou CLI que suporte MCP. O desenvolvedor indexa uma vez; o agente navega por conceitos, símbolos e documentação com precisão e economia de tokens, sem expor código proprietário a serviços externos.

**Por que agora:**
Agentes de IA em editores (Cursor, Claude Code, Cline, Copilot) dependem cada vez mais de contexto do repositório, mas ler arquivos inteiros ou fazer buscas textuais amplas é lento, caro e impreciso. Simultaneamente, preocupações com privacidade e compliance impedem o envio de código para nuvem. A combinação de protocolo MCP padronizado, modelos de embedding locais leves e indexação incremental viabiliza uma solução offline que se integra nativamente ao fluxo do agente.

---

## 2. PROBLEMA

### 2.1 Declaração do Problema

> Desenvolvedores que usam agentes de IA para codificar têm dificuldade em localizar implementações, conceitos e documentação relevantes em bases de código grandes porque ferramentas nativas (grep, leitura de arquivos, busca semântica genérica) não entendem a estrutura do código nem priorizam trechos semanticamente relevantes, o que resulta em respostas imprecisas, estouro de contexto, tempo perdido e — quando recorrem a serviços cloud — risco de vazamento de código proprietário.

### 2.2 Dimensionamento do Problema

| Dimensão | Estimativa | Fonte |
|---|---|---|
| Quantas pessoas têm esse problema? | Milhões de devs usando copilots/agentes em IDEs | Tendência de mercado 2025–2026 |
| Com que frequência o problema ocorre? | Diário — a cada tarefa de exploração ou implementação | Comportamento observado de agentes |
| Qual o custo atual do problema? | Minutos a horas por sessão; dezenas de milhares de tokens por consulta mal direcionada | Constituição do produto (economia de contexto) |

### 2.3 Soluções Atuais e Por Que Falham

| Solução Atual | Por que é insuficiente | Nossa vantagem |
|---|---|---|
| Grep / busca textual | Não captura semântica; exige saber o nome exato do símbolo | Busca híbrida: significado + termos exatos |
| Leitura ampla de arquivos pelo agente | Estoura janela de contexto; "lost in the middle" | Retorna apenas chunks relevantes (símbolos, seções) |
| Busca semântica em nuvem | Envia código a terceiros; requer rede | 100% local e offline |
| `@codebase` genérico do editor | Caixa-preta; sem controle de índice ou staleness | Diagnóstico explícito, reindexação sob demanda |
| Mapa mental manual / docs desatualizados | Esforço humano contínuo; diverge do código | Índice derivado do código real, incremental |

---

## 3. PERSONAS

### Persona Primária: Desenvolvedor com agente de IA no editor

| Atributo | Descrição |
|---|---|
| **Segmento** | Engenheiro de software em projetos médios/grandes (monorepos, legado, múltiplas linguagens) |
| **Contexto de uso** | Sessões diárias de pair programming com agente; explora código desconhecido antes de implementar |
| **Motivação principal** | Entregar features mais rápido com respostas do agente fundamentadas no código real |
| **Dor principal** | Agente "alucina" caminhos ou gasta tokens lendo arquivos errados |
| **Comportamento atual** | Alterna grep, `@files`, perguntas vagas ao agente; reindexa manualmente quando percebe desatualização |
| **Citação representativa** | *"Preciso que o agente ache onde isso está implementado sem eu saber o nome da função."* |

### Persona Secundária: Mantenedor de ferramentas de IA no time

| Atributo | Descrição |
|---|---|
| **Segmento** | Tech lead, platform engineer ou DevEx |
| **Relação com a primária** | Configura e distribui o servidor MCP para o time |
| **Dor principal** | Onboarding inconsistente entre editores; medo de expor código em SaaS |
| **Ganho esperado** | Instalação padronizada, diagnóstico de índice, política "código não sai da máquina" |

### Anti-Persona (quem NÃO é o usuário-alvo)

Equipes que preferem busca 100% cloud com reranking gerenciado, watch mode contínuo ou painel web de administração. O produto prioriza simplicidade operacional e privacidade local — funcionalidades acessórias complexas estão explicitamente fora de escopo.

---

## 4. PROPOSTA DE VALOR

### 4.1 Value Proposition Statement

**Para** desenvolvedores que codificam com agentes de IA  
**Que** precisam encontrar implementações e conceitos em bases de código grandes  
**O** CodeSteer Atlas **é um** servidor de busca contextual integrado ao editor  
**Que** indexa o repositório por unidades lógicas de código e documentação e responde consultas em linguagem natural com trechos precisos e compactos  
**Diferente de** grep, leitura manual ou busca semântica na nuvem  
**Nosso produto** opera offline, preserva privacidade e expõe capacidades nativas via protocolo MCP para qualquer cliente compatível

### 4.2 Benefícios por Persona

| Persona | Benefício Funcional | Benefício Emocional |
|---|---|---|
| Desenvolvedor com agente | Encontra classes, funções, métodos e seções de docs sem abrir arquivos inteiros | Confiança de que o agente "conhece" o repo |
| Mantenedor de ferramentas | Deploy repetível em Cursor, Claude, Cline, Copilot; status do índice visível | Tranquilidade sobre compliance e privacidade |

---

## 5. OBJETIVOS E MÉTRICAS DE SUCESSO

**Objetivo do MVP** (hipótese central já em validação operacional):
> Provar que agentes de codificação completam tarefas de exploração e implementação com menos tokens e maior precisão quando consomem busca híbrida local indexada por símbolos, em vez de leitura ad hoc de arquivos.

### Métricas de Validação do MVP

| Métrica | Estado Atual | Critério de Sucesso | Prazo | Como medir |
|---|---|---|---|---|
| **Adoção** — Projetos com índice criado | Não medido publicamente | 80% dos devs do time piloto indexam em D+1 | 8 semanas | Contagem de `.code-index` / onboarding |
| **Engajamento** — Consultas `atlas_search` por sessão de agente | Não medido | ≥ 3 buscas bem-sucedidas por sessão de exploração | 8 semanas | Telemetria local opcional / observação |
| **Valor entregue** — Tarefa "achar implementação X" sem grep manual | Baseline: grep/leitura | ≥ 70% das tarefas piloto resolvidas só com Atlas | 8 semanas | Teste guiado com 10 cenários |
| **Retenção** — Reindex após mudanças grandes | Comportamento observado | ≥ 90% dos índices stale reindexados em 24h | 8 semanas | `is_stale` + ação de reindex |

### OKR do Produto

**Objective:** Tornar a descoberta de contexto no código local, privada e nativa para agentes de IA.

| Key Result | Baseline | Target | Prazo |
|---|---|---|---|
| KR1 — Tempo médio para localizar um símbolo/conceito | ~5–15 min (grep + leitura) | < 2 min com busca Atlas | Q3 2026 |
| KR2 — Clientes MCP suportados out-of-the-box | 4 (Cursor, Claude Desktop, Cline, Claude Code) | 6+ incl. Copilot e Kiro | Q3 2026 |
| KR3 — Indexação incremental pós-commit | Implementado | Reindex parcial < 30s em repos médios | Q3 2026 |

---

## 6. ESCOPO DO MVP

### 6.1 Princípios do MVP

- **Privacidade absoluta:** nenhum byte de código-fonte sai da máquina do desenvolvedor
- **Economia de contexto:** `atlas_search` retorna metadados por padrão (`include_content=false`); detalhe com `Read` nas linhas ou `include_content=true` só quando necessário
- **Fricção mínima:** instalação via comando remoto; descoberta automática do índice no projeto
- **Resiliência do protocolo:** comunicação MCP estável, sem corrupção do canal de mensagens
- **Simplicidade (YAGNI):** sem watch mode, sem UI web, sem reranking cloud

### 6.2 Capacidades do MVP (Incluídas)

| Capacidade | Persona | Valor entregue | Prioridade |
|---|---|---|---|
| Indexação por estrutura do código (classes, funções, métodos) | Dev | Chunks coerentes, não blocos arbitrários de linhas | Must-have |
| Indexação de documentação (Markdown, configs) | Dev | Busca unificada código + docs | Must-have |
| Busca híbrida semântica + textual com fusão de rankings | Dev / Agente | Precisão em consultas vagas e literais | Must-have |
| Mapa hierárquico de símbolos do workspace | Dev / Agente | Visão estrutural sem carregar arquivos | Must-have |
| Indexação incremental (só arquivos alterados) | Dev | Reindex rápido após commits | Must-have |
| Ferramentas MCP: buscar, mapear, indexar, status | Agente | Contrato estável para clientes | Must-have |
| Diagnóstico de índice (existência, staleness, idiomas) | Mantenedor | Detecta misconfig e desatualização | Must-have |
| Deploy automatizado em editores principais | Mantenedor | Onboarding em minutos | Must-have |
| Reindex assíncrono em background | Dev | Não bloqueia sessão do agente | Should-have |
| Resolução de links em documentação indexada | Dev | Navegação entre notas/docs relacionadas | Should-have |
| Modo dry-run de indexação | Mantenedor | Estimar impacto antes de persistir | Should-have |

### 6.3 Fora do MVP (Explícito)

- **[FE-01]** Watch mode / sincronização contínua automática — complexidade e acoplamento; indexação sob demanda ou startup basta
- **[FE-02]** Interface web de administração do índice — viola princípio YAGNI
- **[FE-03]** Reranking ou embeddings em serviços cloud — conflita com privacidade local
- **[FE-04]** Busca multi-repositório federada — escopo v1 é workspace único com filtro opcional por repo
- **[FE-05]** Edição ou escrita no código via Atlas — produto é leitura/descoberta, não IDE

### 6.4 Roadmap Indicativo Pós-MVP

| Fase | Foco | Gatilho para começar |
|---|---|---|
| MVP (atual) | 4 tools MCP + indexação incremental + deploy multi-cliente | Hoje |
| v1.1 | Métricas de uso local, `.atlasignore` avançado, mais linguagens | KR1 validado em piloto |
| v2.0 | Extensões de chunking (call graphs, refs cruzadas), plugins por domínio | Demanda recorrente em issues |

---

## 7. USER STORIES

**[US-01] — Buscar implementação por conceito**
Como desenvolvedor com agente de IA, quero descrever em linguagem natural o que procuro, para receber trechos de código ranqueados por relevância sem abrir arquivos inteiros.

**Critério de aceite mínimo de produto:**
- Deve retornar lista ordenada com caminho, símbolo, linhas e score
- Deve aceitar filtros por pasta, linguagem e repositório
- Deve permitir omitir conteúdo completo para economizar tokens
- Não deve exigir chamada de diagnóstico prévia à busca

---

**[US-02] — Explorar estrutura do projeto**
Como desenvolvedor com agente de IA, quero uma visão em árvore de classes, métodos e funções, para entender a arquitetura antes de buscar detalhes.

**Critério de aceite mínimo de produto:**
- Deve retornar hierarquia compacta até profundidade configurável
- Deve incluir seções de documentação indexadas
- Deve funcionar com filtro por prefixo de caminho
- Não deve carregar conteúdo completo dos arquivos

---

**[US-03] — Indexar workspace**
Como desenvolvedor, quero indexar meu projeto (completo ou parcial), para que buscas subsequentes reflitam o código atual.

**Critério de aceite mínimo de produto:**
- Deve processar apenas arquivos novos/alterados em modo incremental
- Deve permitir reindex completo quando necessário
- Deve estimar impacto em modo dry-run sem persistir
- Deve ignorar tentativa concorrente com feedback claro (`reindex_in_progress`)
- Deve pedir confirmação do usuário antes de indexar via agente

---

**[US-04] — Diagnosticar saúde do índice**
Como mantenedor de ferramentas, quero verificar se o índice existe, está atualizado e onde está armazenado, para orientar troubleshooting.

**Critério de aceite mínimo de produto:**
- Deve indicar existência, contagem de chunks, linguagens e modelo de embedding
- Deve sinalizar staleness quando o commit Git diverge do indexado
- Deve indicar se reindexação está em andamento
- Não deve modificar o índice

---

**[US-05] — Instalar em editor compatível**
Como mantenedor de ferramentas, quero registrar o servidor no meu editor com um comando, para que agentes passem a usar as ferramentas Atlas.

**Critério de aceite mínimo de produto:**
- Deve suportar modo local (clone) e remoto (sem paths absolutos)
- Deve fazer backup antes de alterar config existente corrompida
- Deve validar dependências críticas no setup
- Deve documentar clientes com config manual (Copilot VS Code, OpenCode)

---

**[US-06] — Recuperar de índice ausente**
Como agente de IA, quero receber erro acionável quando o índice não existe, para instruir o usuário a indexar.

**Critério de aceite mínimo de produto:**
- Deve explicar como criar o índice (referência à ferramenta de indexação)
- Não deve falhar silenciosamente com lista vazia ambígua

---

## 8. JORNADA DO USUÁRIO — FLUXO CORE

```
ENTRADA: Desenvolvedor abre projeto em editor com MCP; Atlas ainda não indexado

1. Usuário executa indexação inicial do workspace (CLI ou tool MCP)
2. Produto varre arquivos suportados, ignora diretórios irrelevantes, fatia por símbolos
3. Produto gera embeddings localmente e persiste índice em pasta oculta do projeto
4. Usuário reinicia ou conecta servidor MCP no editor
5. Agente invoca busca com descrição natural ("onde está o lock de reindex?")
6. Produto embeda query, executa busca híbrida, retorna chunks ranqueados
   └─ Se índice stale: status indica reindex recomendado; busca ainda funciona com dados anteriores
   └─ Se sem matches: array vazio explícito
7. Agente usa metadados (path, linhas, símbolo) para ler apenas trechos necessários
8. Resultado: tarefa de exploração concluída com fração dos tokens de leitura total

SAÍDA: Desenvolvedor implementa ou corrige com contexto preciso; código nunca saiu da máquina
```

---

## 9. REQUISITOS DE PRODUTO

### 9.1 Funcionais

- **[RP-01]** O produto deve indexar código-fonte em unidades lógicas (classe, função, método) e fallback para módulo inteiro quando não houver símbolos parseáveis
- **[RP-02]** O produto deve indexar documentação Markdown e arquivos de configuração textuais suportados
- **[RP-03]** O produto deve combinar busca por similaridade semântica e busca textual, fundindo resultados em ranking único
- **[RP-04]** O produto deve permitir filtrar buscas e mapas por prefixo de caminho, linguagem e repositório
- **[RP-05]** O produto deve detectar arquivos alterados via hash de conteúdo e reindexar apenas o delta
- **[RP-06]** O produto deve remover chunks de arquivos deletados do índice incremental
- **[RP-07]** O produto deve expor quatro capacidades MCP: busca, mapa, indexação e status
- **[RP-08]** O produto deve bloquear indexações concorrentes com feedback explícito ao usuário
- **[RP-09]** O produto deve validar compatibilidade de versão do índice e orientar reindex completo quando incompatível
- **[RP-10]** O produto deve resolver links entre documentos Markdown indexados e incluí-los nos resultados de busca
- **[RP-11]** O produto deve ignorar arquivos acima de limite de tamanho e diretórios configurados como irrelevantes
- **[RP-12]** O produto deve truncar chunks oversized preservando assinatura do símbolo

### 9.2 Não-Funcionais (de produto)

- **[RNF-01] Performance percebida:** Busca deve responder em tempo útil para sessão interativa (< 3s percebidos em repos médios indexados)
- **[RNF-02] Privacidade:** Nenhum código-fonte ou embedding deve ser transmitido a serviços externos durante operação normal
- **[RNF-03] Offline:** Busca e indexação devem funcionar sem conexão após setup inicial (download de modelo)
- **[RNF-04] Onboarding:** Desenvolvedor deve indexar e buscar em ≤ 15 minutos seguindo README, sem suporte humano
- **[RNF-05] Plataformas:** macOS e Linux; Python 3.11–3.13; clientes MCP via stdio
- **[RNF-06] Confiabilidade do protocolo:** Canal MCP não deve ser corrompido por logs ou warnings de dependências
- **[RNF-07] Portabilidade:** Modo remoto não deve exigir caminhos absolutos no manifest do cliente

---

## 10. MODELO DE NEGÓCIO

| Aspecto | Decisão | Impacto no produto |
|---|---|---|
| Monetização | Open source / gratuito | Sem paywall; adoção via comunidade e integrações |
| Aquisição principal | GitHub, plugins de editores, documentação visual | Foco em instalação one-liner (`uvx`) e powers/plugins |
| Retenção | Valor cresce com índice atualizado e hábito do agente | Status de staleness e reindex incremental reduzem abandono |

---

## 11. RISCOS E PREMISSAS

### 11.1 Hipóteses Críticas

| Hipótese | Como validar | Critério de invalidação |
|---|---|---|
| Agentes usam busca proativamente quando instruídos | Observar sessões piloto com regra AGENTS.md | < 50% das sessões usam busca antes de grep |
| Indexação por símbolos supera chunking por linhas | Comparar precisão @10 em 20 queries | Sem ganho mensurável vs baseline textual |
| Offline não é barreira vs cloud | Entrevistas com 5 teams enterprise | > 60% preferem cloud mesmo com risco |

### 11.2 Riscos

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Índice desatualizado gera respostas erradas | Média | Alto | `is_stale` + instruções em AGENTS.md para reindex |
| Segunda indexação ignorada silenciosamente | Média | Médio | Retorno explícito `skipped_reason=reindex_in_progress` |
| Manifest de versão antiga incompatível | Baixa | Alto | Erro acionável com instrução `--full` |
| Cold start no primeiro embedding | Alta | Baixo | Lazy load documentado; reindex async no startup |
| Agente não chama Atlas apesar de disponível | Média | Alto | Docstrings proativas; regras em CLAUDE.md/AGENTS.md |
| Filtros restritivos retornam menos que esperado | Média | Baixo | Documentar `CANDIDATES_LIMIT` como limitação de produto |

---

## 12. DECISÕES EM ABERTO

| # | Decisão | Opções | Severidade | Impacto se não resolvida | Owner | Prazo |
|---|---|---|---|---|---|---|
| D1 | Telemetria opt-in de uso local | Sem telemetria / Opt-in anônimo | 🔵 Opcional | Dificulta medir KR1–KR3 | PM | 2026-07-15 |
| D2 | Suporte Windows nativo | Escopo v1.1 vs v2 | 🟡 Bloqueia execução | Expansão de mercado limitada | PM + Eng | 2026-08-01 |
| D3 | Política de indexação automática no startup | Sempre async / Só manual / Configurável | 🔵 Opcional | Experiência inconsistente entre clientes | PM | 2026-07-01 |

---

## 13. ESCOPO PARA ENGENHARIA

```
Contexto para especificação técnica:

Produto: CodeSteer Atlas
PRD versão: v1.0
Tipo: Produto Novo (MVP) — retrospectivo do vault

Problema a resolver:
Desenvolvedores com agentes de IA não conseguem localizar contexto relevante em codebases grandes de forma privada, rápida e econômica em tokens.

Personas principais:
- Desenvolvedor com agente no editor: quer busca por conceito sem grep manual
- Mantenedor DevEx: quer deploy padronizado e diagnóstico de índice

User Stories do MVP:
- US-01: Busca híbrida por linguagem natural com filtros e economia de tokens
- US-02: Mapa hierárquico de símbolos e seções
- US-03: Indexação incremental/completa com dry-run e lock
- US-04: Status de índice (existência, stale, reindexing)
- US-05: Deploy multi-cliente MCP
- US-06: Erros acionáveis quando índice ausente

Comportamentos obrigatórios:
- RP-01 a RP-12 (ver seção 9.1)
- RNF-01 a RNF-07 (ver seção 9.2)

Fora de escopo:
- FE-01 watch mode contínuo
- FE-02 UI web admin
- FE-03 reranking cloud
- FE-04 busca federada multi-repo
- FE-05 escrita/edição de código

Métricas de validação:
- Tempo para localizar símbolo: < 2 min
- 70% tarefas piloto sem grep manual
- 90% índices stale reindexados em 24h
```

---

## 14. METADADOS

| Campo | Valor |
|---|---|
| Tipo de PRD | Produto Novo (Greenfield) — retrospectivo |
| Confiança | 82/100 |
| User Stories | 6 |
| Hipóteses Críticas | 3 |
| Decisões em Aberto | 3 |
| Riscos Identificados | 6 |
| Domínios mapeados (vault) | Indexação, Busca, Armazenamento, MCP Server, Deploy, Embeddings |
| Tools MCP mapeadas | atlas_search, atlas_map, atlas_index, atlas_status |
| Versão | v1.0 |

---

## Hipóteses críticas não validadas

- Métricas de adoção/engajamento (KR1–KR3) são inferidas — não há telemetria no produto hoje
- Personas derivadas do posicionamento README/constituição, não de entrevistas formais
- Targets numéricos (70%, 90%, < 2 min) são propostos para validação futura, não medidos
