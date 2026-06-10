import json
import sys
from pathlib import Path

import pytest

# Garante que o módulo deploy_mcp.py (na raiz do repo) seja importável
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import deploy_mcp  # noqa: E402


@pytest.mark.parametrize("system_name", ["Darwin", "Windows", "Linux"])
def test_get_mcp_config_paths_claude_desktop_filename(monkeypatch, tmp_path, system_name):
    """Em todos os OSs, o caminho do Claude Desktop usa 'claude_desktop_config.json'."""
    monkeypatch.setattr(deploy_mcp.platform, "system", lambda: system_name)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    if system_name == "Windows":
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    elif system_name == "Linux":
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    paths = deploy_mcp.get_mcp_config_paths()

    assert "Claude Desktop" in paths
    assert paths["Claude Desktop"].name == "claude_desktop_config.json"


def test_get_mcp_config_paths_darwin_layout(monkeypatch, tmp_path):
    """Verifica o layout completo de paths no macOS."""
    monkeypatch.setattr(deploy_mcp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    paths = deploy_mcp.get_mcp_config_paths()

    expected = (
        tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    )
    assert paths["Claude Desktop"] == expected


def test_get_mcp_config_paths_windows_layout(monkeypatch, tmp_path):
    """Verifica o layout completo de paths no Windows."""
    monkeypatch.setattr(deploy_mcp.platform, "system", lambda: "Windows")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    appdata = tmp_path / "AppData" / "Roaming"
    userprofile = tmp_path
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("USERPROFILE", str(userprofile))

    paths = deploy_mcp.get_mcp_config_paths()

    assert paths["Claude Desktop"] == appdata / "Claude" / "claude_desktop_config.json"
    assert paths["Cursor Global"] == userprofile / ".cursor" / "mcp.json"


def test_get_mcp_config_paths_linux_layout(monkeypatch, tmp_path):
    """Verifica o layout completo de paths no Linux (respeitando XDG_CONFIG_HOME)."""
    monkeypatch.setattr(deploy_mcp.platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    config_home = tmp_path / "myconfig"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    paths = deploy_mcp.get_mcp_config_paths()

    assert paths["Claude Desktop"] == config_home / "Claude" / "claude_desktop_config.json"


def test_save_mcp_config_creates_file_and_merges(tmp_path):
    """`save_mcp_config` cria o arquivo, diretórios pais e mescla a chave mcpServers."""
    config_path = tmp_path / "subdir" / "claude_desktop_config.json"

    server_config = {"command": "uv", "args": ["run", "atlas-serve"], "env": {}}

    assert deploy_mcp.save_mcp_config(config_path, server_config) is True
    assert config_path.exists()

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["mcpServers"][deploy_mcp.SERVER_NAME] == server_config


def test_save_mcp_config_backup_on_corrupted_json(tmp_path):
    """Se o JSON existente estiver corrompido, cria backup e reinicializa o arquivo."""
    config_path = tmp_path / "claude_desktop_config.json"
    config_path.write_text("{ this is not valid json", encoding="utf-8")

    server_config = {"command": "uv", "args": ["run", "atlas-serve"], "env": {}}

    assert deploy_mcp.save_mcp_config(config_path, server_config) is True

    # Backup criado
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == "{ this is not valid json"

    # Arquivo principal reinicializado com a config válida
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["mcpServers"][deploy_mcp.SERVER_NAME] == server_config


def test_save_mcp_config_preserves_existing_servers(tmp_path):
    """Mescla preservando outras entradas já existentes em mcpServers."""
    config_path = tmp_path / "claude_desktop_config.json"
    existing = {"mcpServers": {"other-server": {"command": "foo", "args": []}}}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    server_config = {"command": "uv", "args": ["run", "atlas-serve"], "env": {}}

    deploy_mcp.save_mcp_config(config_path, server_config)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "other-server" in data["mcpServers"]
    assert data["mcpServers"][deploy_mcp.SERVER_NAME] == server_config


def test_build_local_server_config_contains_index_dir_and_env(tmp_path):
    """server_config local contém '--index-dir' absoluto nos args e env ATLAS_INDEX_DIR."""
    index_dir_abs = str(tmp_path / ".code-index")

    config = deploy_mcp.build_local_server_config("uv", str(tmp_path), index_dir_abs)

    assert "--index-dir" in config["args"]
    idx = config["args"].index("--index-dir")
    assert config["args"][idx + 1] == index_dir_abs
    assert config["env"]["ATLAS_INDEX_DIR"] == index_dir_abs


def test_build_remote_server_config_contains_index_dir_and_env(tmp_path):
    """server_config remoto contém '--index-dir' absoluto nos args e env ATLAS_INDEX_DIR."""
    index_dir_abs = str(tmp_path / ".code-index")

    config = deploy_mcp.build_remote_server_config("uvx", index_dir_abs)

    assert "--index-dir" in config["args"]
    idx = config["args"].index("--index-dir")
    assert config["args"][idx + 1] == index_dir_abs
    assert config["env"]["ATLAS_INDEX_DIR"] == index_dir_abs


def test_github_repo_url_has_no_extra_hyphen():
    """`GITHUB_REPO_URL` aponta para codesteer-atlas.git sem hífen extra."""
    assert deploy_mcp.GITHUB_REPO_URL == "git+https://github.com/LuisCarlosLopes/codesteer-atlas.git"
    assert "/-codesteer-atlas" not in deploy_mcp.GITHUB_REPO_URL


def test_run_check_returns_zero_on_success():
    """`run_check()` retorna 0 quando todos os imports críticos funcionam."""
    assert deploy_mcp.run_check() == 0


def test_run_check_returns_one_on_failure(monkeypatch):
    """`run_check()` retorna 1 quando algum import crítico falha."""
    monkeypatch.setattr(deploy_mcp, "CRITICAL_MODULES", ["this_module_does_not_exist_xyz"])

    assert deploy_mcp.run_check() == 1
