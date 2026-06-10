# ==============================================================================
# setup.ps1 — Bootstrap idempotente do CodeSteer Atlas MCP (PowerShell 5+)
#
# Wrapper fino: verifica `uv`, sincroniza dependências e delega a validação
# de imports críticos a `deploy_mcp.py --check` (DECISÃO-004).
#
# Uso:
#   .\setup.ps1
# ==============================================================================

$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

Write-Host "CodeSteer Atlas — Bootstrap"
Write-Host ""

# 1. Verifica se uv está instalado
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv não encontrado. Instale com:"
    Write-Host '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    exit 1
}
Write-Host "uv encontrado: $(uv --version)"

# 2. Sincroniza dependências (produção + dev)
Write-Host "Sincronizando dependências..."
uv sync --group dev

# 3. Valida imports críticos via deploy_mcp.py --check
Write-Host "Validando imports críticos..."
uv run python deploy_mcp.py --check

Write-Host ""
Write-Host "Setup concluído. Próximos passos:"
Write-Host "  1. Indexar workspace: uv run atlas-index --workspace ."
Write-Host "  2. Iniciar servidor:  uv run atlas-serve"
Write-Host "  3. Rodar testes:      uv run pytest -v"
