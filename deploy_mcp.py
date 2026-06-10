#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
deploy_mcp.py
Script to deploy the CodeSteer Atlas MCP server to Cursor, Claude Desktop, Cline
and Claude Code CLI. Supports Windows, macOS, and Linux.

Modes:
  python deploy_mcp.py            Interactive installer (prompts for workspace/clients)
  python deploy_mcp.py --check    Validates critical imports (no prompts); exit code
                                   0 on success, 1 on failure. Used by setup.sh/setup.ps1.
"""

import os
import sys
import json
import platform
import shutil
import subprocess
from pathlib import Path

# Configurações globais
SERVER_NAME = "codesteer-atlas"
GITHUB_REPO_URL = "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git"
INDEX_DIR_NAME = ".code-index"

# Imports críticos validados pelo modo --check (substitui a validação do setup.sh)
CRITICAL_MODULES = [
    "fastmcp",
    "lancedb",
    "tree_sitter_language_pack",
    "fastembed",
    "pydantic",
    "click",
    "mcp",
]


def get_uv_paths():
    """
    Detects the absolute paths of uv and uvx executables on the system.
    Returns a tuple (uv_path, uvx_path).
    """
    system = platform.system()
    is_windows = system == "Windows"

    uv_exe = "uv.exe" if is_windows else "uv"
    uvx_exe = "uvx.exe" if is_windows else "uvx"

    # 1. Tenta encontrar no PATH do sistema
    uv_path = shutil.which(uv_exe)
    uvx_path = shutil.which(uvx_exe)

    # 2. Se não encontrou, busca em caminhos comuns de instalação
    home = Path.home()
    if not uv_path:
        search_dirs = []
        if is_windows:
            search_dirs = [
                home / ".local" / "bin",
                Path(os.environ.get("APPDATA", "")) / "local" / "bin",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "uv",
            ]
        else:  # macOS / Linux
            search_dirs = [
                home / ".local" / "bin",
                Path("/opt/homebrew/bin"),
                Path("/usr/local/bin"),
                Path("/usr/bin"),
            ]

        for s_dir in search_dirs:
            potential_uv = s_dir / uv_exe
            if potential_uv.exists():
                uv_path = str(potential_uv)
                break

    if not uvx_path:
        # Tenta achar uvx na mesma pasta que o uv
        if uv_path:
            potential_uvx = Path(uv_path).parent / uvx_exe
            if potential_uvx.exists():
                uvx_path = str(potential_uvx)

    # Fallback para execução genérica se nada for detectado
    if not uv_path:
        uv_path = "uv"
    if not uvx_path:
        uvx_path = "uvx"

    return uv_path, uvx_path


def get_mcp_config_paths():
    """
    Returns a dict mapping editor names to their resolved configuration file paths.

    [A] Claude Desktop usa 'claude_desktop_config.json' (não 'mcpConfig.json') nos
    três sistemas operacionais.
    """
    system = platform.system()
    paths = {}
    home = Path.home()

    if system == "Darwin":  # macOS
        paths["Claude Desktop"] = (
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
        paths["Cursor Global"] = home / ".cursor" / "mcp.json"
        paths["Cline (VS Code)"] = (
            home
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "settings"
            / "cline_mcp_settings.json"
        )
        paths["Cline (CLI)"] = home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"
    elif system == "Windows":
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        userprofile = Path(os.environ.get("USERPROFILE", str(home)))
        paths["Claude Desktop"] = appdata / "Claude" / "claude_desktop_config.json"
        paths["Cursor Global"] = userprofile / ".cursor" / "mcp.json"
        paths["Cline (VS Code)"] = (
            appdata
            / "Code"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "settings"
            / "cline_mcp_settings.json"
        )
        paths["Cline (CLI)"] = userprofile / ".cline" / "data" / "settings" / "cline_mcp_settings.json"
    else:  # Linux
        config_dir = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
        paths["Claude Desktop"] = config_dir / "Claude" / "claude_desktop_config.json"
        paths["Cursor Global"] = home / ".cursor" / "mcp.json"
        paths["Cline (VS Code)"] = (
            config_dir
            / "Code"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "settings"
            / "cline_mcp_settings.json"
        )
        paths["Cline (CLI)"] = home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"

    # Cursor Local (relativo à pasta do projeto atual)
    paths["Cursor Local (Projeto)"] = Path(".cursor") / "mcp.json"

    return paths


def create_backup(file_path):
    """
    Creates a backup copy of the file with a .bak extension.
    """
    path = Path(file_path)
    if path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        try:
            shutil.copy2(path, backup_path)
            print(f"  [Backup] Cópia de segurança criada em: {backup_path.name}")
            return True
        except Exception as e:
            print(f"  [Erro] Falha ao criar cópia de segurança para {path.name}: {e}")
    return False


def save_mcp_config(file_path, server_config):
    """
    Safely merges the server config into the specified JSON file.
    Creates parent directories and the file if they don't exist.

    Em caso de JSON corrompido, cria backup e reinicializa o arquivo
    (comportamento preservado).
    """
    path = Path(file_path)

    # Cria os diretórios pais se não existirem
    path.parent.mkdir(parents=True, exist_ok=True)

    # Cria backup antes de mexer
    create_backup(path)

    # Carrega dados existentes
    data = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
        except Exception as e:
            print(f"  [Erro] Falha ao ler JSON existente em {path}: {e}")
            print("  Um novo arquivo será inicializado preservando o backup.")
            data = {}

    # Garante a chave mcpServers
    if "mcpServers" not in data:
        data["mcpServers"] = {}

    # Insere ou atualiza o servidor
    data["mcpServers"][SERVER_NAME] = server_config

    # Salva o arquivo de volta formatado
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  [Sucesso] Configuração gravada em: {path}")
        return True
    except Exception as e:
        print(f"  [Erro] Falha ao escrever arquivo {path}: {e}")
        return False


def build_local_server_config(uv_path: str, project_dir: str, index_dir_abs: str) -> dict:
    """
    Monta o server_config para o modo LOCAL, injetando '--index-dir' absoluto
    nos args e a variável de ambiente ATLAS_INDEX_DIR como redundância [C].
    """
    return {
        "command": uv_path,
        "args": [
            "--directory",
            project_dir,
            "run",
            "atlas-serve",
            "--index-dir",
            index_dir_abs,
        ],
        "env": {
            "ATLAS_INDEX_DIR": index_dir_abs,
        },
    }


def build_remote_server_config(uvx_path: str, index_dir_abs: str) -> dict:
    """
    Monta o server_config para o modo REMOTO (uvx + GitHub), injetando
    '--index-dir' absoluto e env ATLAS_INDEX_DIR [C].
    """
    return {
        "command": uvx_path,
        "args": [
            "--from",
            GITHUB_REPO_URL,
            "atlas-serve",
            "--index-dir",
            index_dir_abs,
        ],
        "env": {
            "ATLAS_INDEX_DIR": index_dir_abs,
        },
    }


def setup_claude_code_cli(server_config: dict) -> bool:
    """
    Registra o servidor no Claude Code CLI via `claude mcp add`, se o binário
    `claude` estiver disponível no PATH [P].
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("  [Aviso] Binário 'claude' não encontrado no PATH. Pulando Claude Code CLI.")
        return False

    command = [claude_bin, "mcp", "add", SERVER_NAME, "--"] + [
        server_config["command"],
        *server_config["args"],
    ]

    try:
        env = os.environ.copy()
        env.update(server_config.get("env", {}))
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if result.returncode == 0:
            print(f"  [Sucesso] Servidor registrado no Claude Code CLI ('{claude_bin} mcp add').")
            return True
        else:
            print(f"  [Erro] 'claude mcp add' falhou: {result.stderr.strip() or result.stdout.strip()}")
            return False
    except Exception as e:
        print(f"  [Erro] Falha ao executar 'claude mcp add': {e}")
        return False


def run_check() -> int:
    """
    Modo --check: valida que os imports críticos funcionam no ambiente atual
    (substitui a validação que vivia no setup.sh). Cross-platform (Python puro).

    Retorna 0 em sucesso, 1 em falha.
    """
    print("Validando imports críticos do CodeSteer Atlas...")
    failed = []

    for module in CRITICAL_MODULES:
        try:
            __import__(module)
            print(f"  [OK] {module}")
        except Exception as e:
            print(f"  [FALHA] {module} — {e}")
            failed.append(module)

    try:
        from codesteer_atlas.server import app  # noqa: F401

        print("  [OK] codesteer_atlas.server")
    except Exception as e:
        print(f"  [FALHA] codesteer_atlas.server — {e}")
        failed.append("codesteer_atlas.server")

    print()
    if failed:
        print(f"VALIDAÇÃO FALHOU: {len(failed)} módulo(s) com erro: {', '.join(failed)}")
        return 1

    print("VALIDAÇÃO CONCLUÍDA COM SUCESSO.")
    return 0


def main():
    print("╔═════════════════════════════════════════╗")
    print("║        CodeSteer Atlas MCP — Instalador        ║")
    print("╚═════════════════════════════════════════╝")
    print()

    # Resolve caminhos do uv
    uv_path, uvx_path = get_uv_paths()
    project_dir = str(Path(__file__).resolve().parent)

    # Limpa caminhos para exibição bonita no Windows
    if platform.system() == "Windows":
        project_dir = project_dir.replace("\\", "/")
        uv_path = uv_path.replace("\\", "/")
        uvx_path = uvx_path.replace("\\", "/")

    print(f"Diretório do projeto detectado: {project_dir}")
    print(f"Executável uv resolvido: {uv_path}")
    print(f"Executável uvx resolvido: {uvx_path}")
    print()

    # Pergunta o workspace a indexar (default: CWD) e injeta --index-dir absoluto [C]
    default_workspace = str(Path.cwd().resolve())
    workspace_input = input(
        f"Qual workspace deve ser indexado? [padrão: {default_workspace}]: "
    ).strip()
    workspace_path = Path(workspace_input).resolve() if workspace_input else Path(default_workspace)
    index_dir_abs = str((workspace_path / INDEX_DIR_NAME).resolve())

    if platform.system() == "Windows":
        index_dir_abs = index_dir_abs.replace("\\", "/")

    print(f"\nWorkspace selecionado: {workspace_path}")
    print(f"Diretório do índice (--index-dir / ATLAS_INDEX_DIR): {index_dir_abs}")

    # Escolha de Modo (Local vs Remoto)
    print("\nEscolha o Modo de Instalação:")
    print(" [1] Local (Desenvolvimento - usa a pasta física deste repositório)")
    print(" [2] Remoto (Produção - roda direto do GitHub usando uvx, sem acoplamento)")

    mode = ""
    while mode not in ["1", "2"]:
        mode = input("Digite a opção (1 ou 2): ").strip()

    if mode == "1":
        print("\n→ Configurando em modo LOCAL...")
        server_config = build_local_server_config(uv_path, project_dir, index_dir_abs)
    else:
        print("\n→ Configurando em modo REMOTO (GitHub)...")
        server_config = build_remote_server_config(uvx_path, index_dir_abs)

    # Exibe a configuração que será aplicada
    print("\nConfiguração JSON gerada:")
    print(json.dumps({SERVER_NAME: server_config}, indent=2))
    print()

    # Detecta editores disponíveis
    config_paths = get_mcp_config_paths()
    available_clients = []

    print("Clientes MCP detectados em seu sistema:")
    idx = 1
    client_mapping = {}

    for name, path in config_paths.items():
        # Para o Cursor Local do Projeto, sempre listamos
        # Para os outros, listamos se o diretório pai existir (indicando que o app está instalado/foi usado)
        parent_exists = path.parent.exists()

        status_str = "[Detectado]" if parent_exists or name == "Cursor Local (Projeto)" else "[Não encontrado]"
        print(f" [{idx}] {name:<25} {status_str:<16} Path: {path}")

        client_mapping[str(idx)] = (name, path)
        available_clients.append(str(idx))
        idx += 1

    # Opção adicional: Claude Code CLI [P]
    claude_code_idx = str(idx)
    claude_available = shutil.which("claude") is not None
    status_str = "[Detectado]" if claude_available else "[Não encontrado]"
    print(f" [{claude_code_idx}] {'Claude Code CLI':<25} {status_str:<16} (via 'claude mcp add')")

    print()
    print("Selecione os clientes que deseja configurar (ex: '1,2' ou 'all' para todos os detectados):")
    selection = input("Opções: ").strip().lower()

    targets = []
    use_claude_code_cli = False

    if selection == "all":
        # Pega todos os clientes detectados/existentes
        targets = [
            client_mapping[k]
            for k in available_clients
            if client_mapping[k][1].parent.exists() or client_mapping[k][0] == "Cursor Local (Projeto)"
        ]
        use_claude_code_cli = claude_available
    else:
        parts = [p.strip() for p in selection.split(",")]
        for part in parts:
            if part in client_mapping:
                targets.append(client_mapping[part])
            elif part == claude_code_idx:
                use_claude_code_cli = True

    if not targets and not use_claude_code_cli:
        print("\nNenhum cliente selecionado para instalação. Saindo.")
        sys.exit(0)

    print(f"\nIniciando deploy em {len(targets) + (1 if use_claude_code_cli else 0)} cliente(s)...")
    success_count = 0
    total_count = len(targets) + (1 if use_claude_code_cli else 0)

    for name, path in targets:
        print(f"\nConfigurando {name}...")
        if save_mcp_config(path, server_config):
            success_count += 1

    if use_claude_code_cli:
        print("\nConfigurando Claude Code CLI...")
        if setup_claude_code_cli(server_config):
            success_count += 1

    print("\n" + "═" * 54)
    print(f"Deploy finalizado: {success_count} de {total_count} cliente(s) configurado(s) com sucesso!")
    print("═" * 54)
    print("\nIMPORTANTE: Lembre-se de reiniciar o Claude Desktop, Cursor ou VS Code para aplicar as alterações.")
    print("Se rodou no modo LOCAL, certifique-se de ter rodado './setup.sh' (ou 'setup.ps1' no Windows) antes.")
    print(f"\nLembre-se de indexar o workspace antes do primeiro uso: 'uv run atlas-index --workspace {workspace_path}'")


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(run_check())
    main()
