# Política de Segurança

## Escopo

O CodeSteer Atlas é um servidor MCP **100% local e offline**. O código-fonte indexado permanece na máquina do desenvolvedor; este repositório contém apenas o software do indexador e do servidor MCP.

## Versões suportadas

| Versão | Suportada          |
| ------ | ------------------ |
| 1.x    | :white_check_mark: |

## Reportar uma vulnerabilidade

**Não abra issues públicas para vulnerabilidades de segurança.**

Envie um reporte privado para o mantenedor do repositório:

1. **GitHub Security Advisories** (recomendado): em [Security → Advisories → Report a vulnerability](https://github.com/LuisCarlosLopes/codesteer-atlas/security/advisories/new) no repositório.
2. **E-mail**: entre em contato com o owner do repositório via GitHub (perfil `LuisCarlosLopes`).

Inclua, quando possível:

- Descrição do impacto e passos para reproduzir
- Versão/commit afetado
- Sugestão de mitigação (opcional)

### O que esperar

- Confirmação de recebimento em até **7 dias úteis**
- Avaliação inicial e plano de correção em até **14 dias úteis**
- Coordenação de divulgação responsável antes de publicar detalhes

## Práticas de segurança do projeto

- Dependências monitoradas via **Dependabot**
- Análise estática com **CodeQL** no CI
- CI obrigatório (testes, lint e validação de imports) antes de merge em `main`
- Repositório privado — sem exposição pública do código proprietário
