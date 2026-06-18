# Contribuindo com a Base Cognitiva

## Regras fundamentais

1. **Links são sempre wikilinks** — formato de colchetes duplos com nome do arquivo,
   nunca URLs relativas para notas internas.
2. **Toda nota nasce `draft`** — promoção a `approved` é decisão humana via PR.
3. **Uma nota = uma ideia** — se crescer demais, quebre e conecte com wikilinks.
4. **Termos no glossário** — defina em [[meta/glossary]], não inline nas notas.
5. **IDs são permanentes** — `dec-001`, `spc-002`, `gd-003`, etc. Nunca reutilize.

## Classificação por quadrante

| Pergunta do leitor | Quadrante | Prefixo de ID |
| ------------------ | --------- | ------------- |
| Por que é assim? | `decisions/` | `dec-` |
| O que o sistema faz? | `specs/` | `spc-` |
| Como existe hoje? | `system/` | `sys-` |
| Como fazer dentro da arquitetura? | `guides/` | `gd-` |
| Como operar? | `ops/` | `ops-` |

Correções de código gerado por IA vão em `decisions/ai-corrections/` (type
`ai-correction`).

## Fluxo de trabalho

### Manual

1. Copie [[meta/templates/note]] como ponto de partida (ou use a skill `kb-note`)
2. Preencha front matter obrigatório: `id`, `type`, `title`, `status`, `created`,
   `author`
3. Escreva o corpo com as seções do type
4. Adicione wikilinks narrativos em **Notas Relacionadas**
5. Abra PR — `doc-quality.yml` valida automaticamente

### Automático (doc-agent)

O workflow `doc-agent.yml` analisa PRs e sugere rascunhos de nota com base em:

| Padrão no PR | Quadrante | Type |
| ------------ | --------- | ---- |
| `feat:` | `specs/` | `feature` |
| `fix:` com stack trace | `ops/` | `incident` |
| `refactor:` breaking | `decisions/` | `adr` |
| `chore(migration):` / `.sql` | `system/` | `table` |
| label `ai-correction` | `decisions/ai-corrections/` | `ai-correction` |
| label `guide` / `docs(guide):` | `guides/` | `how-to` |

Revise o rascunho gerado antes de promover a `approved`.

## Status de notas

| Status | Significado |
| ------ | ----------- |
| `draft` | Rascunho, pode mudar livremente |
| `approved` | Revisado e confiável |
| `superseded` | Substituído por outra nota (linkar a sucessora) |

## Validação em PR

O workflow `doc-quality.yml` verifica:

- Front matter com campos obrigatórios
- IDs duplicados
- Wikilinks quebrados (arquivo referenciado não existe)

## Convenções de subpastas

- `decisions/`, `specs/`, `system/`, `ops/` → por **domínio de negócio**
  (`indexacao/`, `busca/`, `deploy/`...) quando o quadrante crescer (> 8 notas)
- `guides/` → por **natureza** (`architecture/`, `framework/`, `patterns/`,
  `onboarding/`)
- Nunca crie subpasta com nome de tipo de documento (`adr/`, `how-to/`)

## Autor

Handle padrão: `@luiscarloslopes`
