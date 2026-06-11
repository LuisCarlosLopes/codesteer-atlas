import json
from pathlib import Path
from unittest.mock import patch
from codesteer_atlas.models import IndexManifest, SearchResult
from codesteer_atlas.server import (
    atlas_status,
    atlas_search,
    atlas_map,
    atlas_index,
    resolve_index_dir,
)

# Mock do manifesto de índice de teste
MOCK_MANIFEST = IndexManifest(
    total_chunks=10,
    repos_indexed=["my-project"],
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    embedding_dim=384,
    embedding_backend="fastembed",
    storage_backend="lancedb",
    last_indexed_at="2026-06-05T12:00:00Z",
    git_head_sha="sha_98765",
    languages_indexed=["python"],
    index_version="2.0.0",
)


def test_atlas_status_endpoint_no_index():
    """
    Testa o retorno da ferramenta atlas_status quando o índice ainda
    não foi criado no filesystem.
    """
    with patch("codesteer_atlas.storage.StorageBackend.exists", return_value=False):
        result_json = atlas_status()
        result = json.loads(result_json)

        assert result["index_exists"] is False
        assert result["total_chunks"] == 0
        assert result["last_indexed_at"] is None


def test_atlas_status_endpoint_with_index():
    """
    Testa o diagnóstico da ferramenta atlas_status quando o índice existe
    e retorna informações válidas do manifest.
    """
    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch("codesteer_atlas.storage.StorageBackend.get_manifest", return_value=MOCK_MANIFEST),
        patch("codesteer_atlas.server.get_git_head_sha", return_value="sha_98765"),
    ):
        result_json = atlas_status()
        result = json.loads(result_json)

        assert result["index_exists"] is True
        assert result["total_chunks"] == 10
        assert "my-project" in result["repos_indexed"]
        assert result["git_head_sha"] == "sha_98765"
        assert result["is_stale"] is False


def test_atlas_search_success():
    """
    Testa a chamada bem-sucedida de atlas_search validando a geração
    de embedding da query e chamada ao storage.
    """
    mock_results = [
        SearchResult(
            file_path="src/app.py",
            start_line=10,
            end_line=20,
            scope_type="function",
            scope_name="run",
            language="python",
            content="def run(): pass",
            score=0.15,
            repo="my-project",
        )
    ]

    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch("codesteer_atlas.storage.StorageBackend.get_manifest", return_value=MOCK_MANIFEST),
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode_single", return_value=[0.0] * 384
        ) as mock_encode,
        patch(
            "codesteer_atlas.storage.StorageBackend.search_hybrid", return_value=mock_results
        ) as mock_search,
    ):
        result_json = atlas_search(query="how to run app", top_k=2)
        result = json.loads(result_json)

        # Garante que a query foi convertida em vetor síncronamente
        mock_encode.assert_called_once_with("how to run app")

        # Garante que a busca híbrida foi disparada com os parâmetros corretos
        mock_search.assert_called_once()

        assert len(result["results"]) == 1
        assert result["results"][0]["symbol"] == "run"
        assert result["results"][0]["score"] == 0.15
        assert result["results"][0]["content"] == "def run(): pass"
        assert result["total_chunks_searched"] == 10

        # JSON sem indentação (separators compactos: sem ', ' nem ': ' fora de strings)
        assert "\n" not in result_json
        assert '", "' not in result_json
        assert '": ' not in result_json


def test_atlas_search_include_content_false_omits_content():
    """`include_content=false` omite o campo 'content' e reduz o payload."""
    mock_results = [
        SearchResult(
            file_path="src/app.py",
            start_line=10,
            end_line=20,
            scope_type="function",
            scope_name="run",
            language="python",
            content="def run(): pass " * 20,  # conteúdo "grande" para comparação de tamanho
            score=0.15,
            repo="my-project",
        )
    ]

    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch("codesteer_atlas.storage.StorageBackend.get_manifest", return_value=MOCK_MANIFEST),
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode_single", return_value=[0.0] * 384
        ),
        patch(
            "codesteer_atlas.storage.StorageBackend.search_hybrid", return_value=mock_results
        ),
    ):
        result_full = atlas_search(query="how to run app", top_k=2, include_content=True)
        result_compact = atlas_search(query="how to run app", top_k=2, include_content=False)

        full = json.loads(result_full)
        compact = json.loads(result_compact)

        assert "content" in full["results"][0]
        assert "content" not in compact["results"][0]

        # Resposta compacta deve ser ao menos 50% menor que a resposta completa
        assert len(result_compact) <= len(result_full) * 0.5


def test_atlas_search_limit_alias_overrides_top_k():
    """`limit` é aceito como alias de `top_k` e sobrescreve seu valor."""
    mock_results = []

    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch("codesteer_atlas.storage.StorageBackend.get_manifest", return_value=MOCK_MANIFEST),
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode_single", return_value=[0.0] * 384
        ),
        patch(
            "codesteer_atlas.storage.StorageBackend.search_hybrid", return_value=mock_results
        ) as mock_search,
    ):
        atlas_search(query="how to run app", top_k=5, limit=10)

        mock_search.assert_called_once()
        assert mock_search.call_args.kwargs["top_k"] == 10


def test_atlas_map_generation():
    """
    Testa o retorno da ferramenta atlas_map formatando os chunks
    em formato de árvore hierárquica legível.
    """
    # Projeção de símbolos (file_path/scope_type/scope_name), sem vector/content
    mock_symbols = [
        {
            "file_path": "src/controllers/user.py",
            "scope_type": "class",
            "scope_name": "UserController",
        },
        {
            "file_path": "src/controllers/user.py",
            "scope_type": "method",
            "scope_name": "UserController.create",
        },
    ]

    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch("codesteer_atlas.storage.StorageBackend.get_symbols", return_value=mock_symbols),
        patch("codesteer_atlas.storage.StorageBackend.get_manifest", return_value=MOCK_MANIFEST),
    ):
        result_json = atlas_map(max_depth=3)
        result = json.loads(result_json)

        assert result["total_files"] == 1
        assert result["total_symbols"] == 2
        assert "UserController" in result["map"]
        assert "UserController.create" in result["map"]
        assert "src/controllers/user.py" in result["map"]

        # Sem emojis na saída
        for char in result["map"]:
            assert ord(char) < 0x1F000, f"Emoji encontrado no map: {char!r}"

        # JSON sem indentação (separators compactos)
        assert "\n" not in result_json or result_json.count("\n") == result["map"].count("\n")

        # Testa compatibilidade ao passar o argumento query opcional (deve ser ignorado)
        result_json_with_query = atlas_map(max_depth=3, query="agents triad flow")
        result_with_query = json.loads(result_json_with_query)
        assert result_with_query["total_files"] == 1
        assert result_with_query["total_symbols"] == 2


def test_resolve_index_dir_precedence_arg_over_env_and_discovery(tmp_path):
    """`--index-dir` (cli_arg) tem prioridade sobre env e discovery."""
    env_dir = tmp_path / "env-index"
    cli_dir = tmp_path / "cli-index"

    result = resolve_index_dir(cli_arg=str(cli_dir), env={"ATLAS_INDEX_DIR": str(env_dir)})

    assert Path(result) == cli_dir


def test_resolve_index_dir_uses_env_when_no_arg(tmp_path):
    """Sem `cli_arg`, usa `ATLAS_INDEX_DIR` quando definido."""
    env_dir = tmp_path / "env-index"

    result = resolve_index_dir(cli_arg=None, env={"ATLAS_INDEX_DIR": str(env_dir)})

    assert Path(result) == env_dir


def test_resolve_index_dir_discovers_ascending_from_subdir(tmp_path):
    """Descobre `.code-index` subindo a partir de um subdiretório do workspace."""
    index_dir = tmp_path / ".code-index"
    index_dir.mkdir()

    sub_dir = tmp_path / "src" / "nested"
    sub_dir.mkdir(parents=True)

    result = resolve_index_dir(cli_arg=None, env={}, start_dir=sub_dir)

    assert Path(result).resolve() == index_dir.resolve()


def test_resolve_index_dir_falls_back_to_default_when_nothing_resolves(tmp_path):
    """Quando nada resolve (sem arg, env ou .code-index ascendente), usa o default."""
    from codesteer_atlas.config import DEFAULT_INDEX_DIR

    isolated_dir = tmp_path / "isolated"
    isolated_dir.mkdir()

    result = resolve_index_dir(cli_arg=None, env={}, start_dir=isolated_dir)

    assert Path(result) == DEFAULT_INDEX_DIR


def test_atlas_index_dry_run_does_not_create_index(tmp_path):
    """`dry_run=true` retorna candidates com eligible_files e não cria/altera o índice."""
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.py").write_text("def main(): pass\n")
    (workspace / "src" / "helper.py").write_text("def helper(): pass\n")
    (workspace / "README.md").write_text("# Project\n")

    with patch("codesteer_atlas.server.INDEX_DIR_PATH", tmp_path / ".code-index"):
        result_json = atlas_index(workspace=str(workspace), dry_run=True)

    result = json.loads(result_json)

    assert not (tmp_path / ".code-index").exists()
    assert "candidates" in result
    paths = {c["path"]: c["eligible_files"] for c in result["candidates"]}
    assert paths.get("src") == 2
    assert paths.get("README.md") == 1
    assert result["total_eligible_files"] == 3


def test_atlas_index_paths_outside_workspace_raises_value_error(tmp_path):
    """`paths` com traversal (ex: '../x') ou absoluto fora do workspace -> ValueError."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("codesteer_atlas.server.INDEX_DIR_PATH", tmp_path / ".code-index"):
        try:
            atlas_index(workspace=str(workspace), paths=["../outside"], dry_run=True)
            assert False, "Esperava ValueError"
        except ValueError:
            pass


def test_atlas_index_docstring_instructs_asking_user():
    """Regressão de contrato: a docstring deve instruir o agente a perguntar ao usuário."""
    doc = atlas_index.__doc__ or ""
    assert "ASK" in doc
    assert "dry_run" in doc
