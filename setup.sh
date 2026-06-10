#!/usr/bin/env bash
# ==============================================================================
# setup.sh — Bootstrap idempotente do CodeSteer Atlas MCP
#
# Wrapper fino: verifica `uv`, sincroniza dependências e delega a validação
# de imports críticos a `deploy_mcp.py --check` (DECISÃO-004).
#
# Uso:
#   chmod +x setup.sh && ./setup.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "CodeSteer Atlas — Bootstrap"
echo ""

# 1. Verifica se uv está instalado
if ! command -v uv &>/dev/null; then
    echo "uv não encontrado. Instale com: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "Ou via Homebrew: brew install uv"
    exit 1
fi
echo "uv encontrado: $(uv --version)"

# 2. Sincroniza dependências (produção + dev)
echo "Sincronizando dependências..."
uv sync --group dev

# 3. Valida imports críticos via deploy_mcp.py --check
echo "Validando imports críticos..."
uv run python deploy_mcp.py --check

echo ""
echo "Setup concluído. Próximos passos:"
echo "  1. Indexar workspace: uv run atlas-index --workspace ."
echo "  2. Iniciar servidor:  uv run atlas-serve"
echo "  3. Rodar testes:      uv run pytest -v"
