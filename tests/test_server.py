import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from codesteer_atlas.models import IndexManifest, IndexStats, SearchResult
from codesteer_atlas.server import (
    atlas_status,
    atlas_search,
    atlas_map,
    atlas_index,
    main,
    resolve_index_dir,
    _spawn_background_reindex,
    _spawn_index_subprocess,
    _safe_responder_respond,
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
    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=False),
        patch("codesteer_atlas.server.is_reindex_locked", return_value=False),
    ):
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
        patch("codesteer_atlas.server.is_reindex_locked", return_value=False),
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


def test_atlas_search_markdown_chunk_with_link_includes_references():
    """Chunk markdown com link para outro .md (sem anchor) ganha `markdown_references`."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="Ver também [outros docs](other.md) para mais detalhes.",
            score=0.2,
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
        result = json.loads(atlas_search(query="docs", top_k=1))

        refs = result["results"][0]["markdown_references"]
        assert refs == [{"file_path": "docs/other.md", "anchor": None, "resolved_section": None}]


def test_atlas_search_markdown_link_with_resolved_anchor():
    """Link com #anchor que corresponde a uma seção indexada (após slugify) resolve `resolved_section`."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="[ver Decisão 007](decisions.md#decisao-007)",
            score=0.2,
            repo="my-project",
        )
    ]
    mock_sections = [
        {"scope_type": "section", "scope_name": "Decisão 007"},
        {"scope_type": "section", "scope_name": "Decisão 008"},
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
        patch(
            "codesteer_atlas.storage.StorageBackend.get_sections_by_file_path",
            return_value=mock_sections,
        ),
    ):
        result = json.loads(atlas_search(query="docs", top_k=1))

        refs = result["results"][0]["markdown_references"]
        assert refs == [
            {
                "file_path": "docs/decisions.md",
                "anchor": "decisao-007",
                "resolved_section": "Decisão 007",
            }
        ]


def test_atlas_search_markdown_link_with_unresolved_anchor_is_null():
    """Link com #anchor sem correspondência em seções indexadas retorna `resolved_section: null`."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="[ver](decisions.md#secao-inexistente)",
            score=0.2,
            repo="my-project",
        )
    ]
    mock_sections = [
        {"scope_type": "section", "scope_name": "Decisão 007"},
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
        patch(
            "codesteer_atlas.storage.StorageBackend.get_sections_by_file_path",
            return_value=mock_sections,
        ),
    ):
        result = json.loads(atlas_search(query="docs", top_k=1))

        refs = result["results"][0]["markdown_references"]
        assert refs == [
            {
                "file_path": "docs/decisions.md",
                "anchor": "secao-inexistente",
                "resolved_section": None,
            }
        ]


def test_atlas_search_markdown_chunk_without_links_omits_field():
    """Chunk markdown sem nenhum link .md não recebe a chave `markdown_references`."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="Apenas texto, sem links para outros documentos.",
            score=0.2,
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
        result = json.loads(atlas_search(query="docs", top_k=1))

        assert "markdown_references" not in result["results"][0]


def test_atlas_search_non_markdown_chunk_omits_field():
    """Chunk não-markdown nunca recebe a chave `markdown_references`, mesmo com texto similar a link."""
    mock_results = [
        SearchResult(
            file_path="src/app.py",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="run",
            language="python",
            content="# [doc](other.md) — comentário com sintaxe de link",
            score=0.2,
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
        result = json.loads(atlas_search(query="docs", top_k=1))

        assert "markdown_references" not in result["results"][0]


def test_atlas_search_include_content_false_still_includes_references():
    """`include_content=false` omite `content`, mas `markdown_references` permanece presente."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="Ver também [outros docs](other.md) para mais detalhes.",
            score=0.2,
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
        result = json.loads(atlas_search(query="docs", top_k=1, include_content=False))

        item = result["results"][0]
        assert "content" not in item
        assert item["markdown_references"] == [
            {"file_path": "docs/other.md", "anchor": None, "resolved_section": None}
        ]


def test_atlas_search_wikilink_resolved_via_manifest_files():
    """[[mcp-server]] com manifest.files contendo 1 mcp-server.md resolve file_path."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="Ver [[mcp-server]] para detalhes.",
            score=0.2,
            repo="my-project",
        )
    ]
    manifest_with_files = MOCK_MANIFEST.model_copy(
        update={"files": {"docs/mcp-server.md": "sha_1"}}
    )

    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch(
            "codesteer_atlas.storage.StorageBackend.get_manifest",
            return_value=manifest_with_files,
        ),
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode_single", return_value=[0.0] * 384
        ),
        patch(
            "codesteer_atlas.storage.StorageBackend.search_hybrid", return_value=mock_results
        ),
    ):
        result = json.loads(atlas_search(query="docs", top_k=1))

        refs = result["results"][0]["markdown_references"]
        assert refs == [
            {"file_path": "docs/mcp-server.md", "anchor": None, "resolved_section": None}
        ]


def test_atlas_search_wikilink_ambiguous_returns_candidates():
    """[[mcp-server]] com 2 arquivos mcp-server.md em manifest.files retorna candidates e file_path=null."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="[[mcp-server]]",
            score=0.2,
            repo="my-project",
        )
    ]
    manifest_with_files = MOCK_MANIFEST.model_copy(
        update={
            "files": {
                "docs/a/mcp-server.md": "sha_1",
                "docs/b/mcp-server.md": "sha_2",
            }
        }
    )

    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch(
            "codesteer_atlas.storage.StorageBackend.get_manifest",
            return_value=manifest_with_files,
        ),
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode_single", return_value=[0.0] * 384
        ),
        patch(
            "codesteer_atlas.storage.StorageBackend.search_hybrid", return_value=mock_results
        ),
    ):
        result = json.loads(atlas_search(query="docs", top_k=1))

        refs = result["results"][0]["markdown_references"]
        assert refs == [
            {
                "file_path": None,
                "anchor": None,
                "resolved_section": None,
                "candidates": ["docs/a/mcp-server.md", "docs/b/mcp-server.md"],
            }
        ]


def test_atlas_search_wikilink_with_alias_includes_alias_field():
    """[[mcp-server|Servidor MCP]] inclui o campo alias em markdown_references."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="[[mcp-server|Servidor MCP]]",
            score=0.2,
            repo="my-project",
        )
    ]
    manifest_with_files = MOCK_MANIFEST.model_copy(
        update={"files": {"docs/mcp-server.md": "sha_1"}}
    )

    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch(
            "codesteer_atlas.storage.StorageBackend.get_manifest",
            return_value=manifest_with_files,
        ),
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode_single", return_value=[0.0] * 384
        ),
        patch(
            "codesteer_atlas.storage.StorageBackend.search_hybrid", return_value=mock_results
        ),
    ):
        result = json.loads(atlas_search(query="docs", top_k=1))

        refs = result["results"][0]["markdown_references"]
        assert refs == [
            {
                "file_path": "docs/mcp-server.md",
                "anchor": None,
                "resolved_section": None,
                "alias": "Servidor MCP",
            }
        ]


def test_atlas_search_wikilink_with_anchor_resolves_section():
    """[[mcp-server#Visão Geral]] com seção indexada resolve resolved_section."""
    mock_results = [
        SearchResult(
            file_path="docs/index.md",
            start_line=1,
            end_line=5,
            scope_type="section",
            scope_name="Intro",
            language="markdown",
            content="[[mcp-server#Visão Geral]]",
            score=0.2,
            repo="my-project",
        )
    ]
    manifest_with_files = MOCK_MANIFEST.model_copy(
        update={"files": {"docs/mcp-server.md": "sha_1"}}
    )
    mock_sections = [
        {"scope_type": "section", "scope_name": "Visão Geral"},
    ]

    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch(
            "codesteer_atlas.storage.StorageBackend.get_manifest",
            return_value=manifest_with_files,
        ),
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode_single", return_value=[0.0] * 384
        ),
        patch(
            "codesteer_atlas.storage.StorageBackend.search_hybrid", return_value=mock_results
        ),
        patch(
            "codesteer_atlas.storage.StorageBackend.get_sections_by_file_path",
            return_value=mock_sections,
        ),
    ):
        result = json.loads(atlas_search(query="docs", top_k=1))

        refs = result["results"][0]["markdown_references"]
        assert refs == [
            {
                "file_path": "docs/mcp-server.md",
                "anchor": "Visão Geral",
                "resolved_section": "Visão Geral",
            }
        ]


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


def test_resolve_index_dir_discovers_via_claude_project_dir_when_cwd_differs(tmp_path):
    """Quando o CWD do processo (ex.: HOME do usuário em plugins) não tem
    `.code-index` ascendente, mas `CLAUDE_PROJECT_DIR` aponta para a raiz do
    projeto que tem, usa a discovery a partir de `CLAUDE_PROJECT_DIR`."""
    project_dir = tmp_path / "project"
    index_dir = project_dir / ".code-index"
    index_dir.mkdir(parents=True)

    other_cwd = tmp_path / "home"
    other_cwd.mkdir()

    result = resolve_index_dir(
        cli_arg=None,
        env={"CLAUDE_PROJECT_DIR": str(project_dir)},
        start_dir=other_cwd,
    )

    assert Path(result).resolve() == index_dir.resolve()


def test_resolve_index_dir_falls_back_to_claude_project_dir_default(tmp_path):
    """Quando nem o CWD nem `CLAUDE_PROJECT_DIR` têm `.code-index`, o default
    passa a ser relativo a `CLAUDE_PROJECT_DIR` (não ao CWD do plugin)."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    other_cwd = tmp_path / "home"
    other_cwd.mkdir()

    result = resolve_index_dir(
        cli_arg=None,
        env={"CLAUDE_PROJECT_DIR": str(project_dir)},
        start_dir=other_cwd,
    )

    assert Path(result).resolve() == (project_dir / ".code-index").resolve()


def test_resolve_index_dir_discovers_via_workspace_folder_paths_when_cwd_differs(tmp_path):
    """Cursor/VS Code expõem a raiz do workspace via `WORKSPACE_FOLDER_PATHS`
    (sem `CLAUDE_PROJECT_DIR`). Quando o CWD do processo não tem `.code-index`
    ascendente, a discovery deve cair para `WORKSPACE_FOLDER_PATHS`."""
    project_dir = tmp_path / "project"
    index_dir = project_dir / ".code-index"
    index_dir.mkdir(parents=True)

    other_dir = tmp_path / "other-workspace"
    other_dir.mkdir()

    other_cwd = tmp_path / "cursor-internal"
    other_cwd.mkdir()

    result = resolve_index_dir(
        cli_arg=None,
        env={"WORKSPACE_FOLDER_PATHS": os.pathsep.join([str(project_dir), str(other_dir)])},
        start_dir=other_cwd,
    )

    assert Path(result).resolve() == index_dir.resolve()


def test_resolve_index_dir_falls_back_to_workspace_folder_paths_default(tmp_path):
    """Quando nem o CWD nem `WORKSPACE_FOLDER_PATHS` têm `.code-index`, o
    default passa a ser relativo ao primeiro path de `WORKSPACE_FOLDER_PATHS`."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    other_cwd = tmp_path / "cursor-internal"
    other_cwd.mkdir()

    result = resolve_index_dir(
        cli_arg=None,
        env={"WORKSPACE_FOLDER_PATHS": str(project_dir)},
        start_dir=other_cwd,
    )

    assert Path(result).resolve() == (project_dir / ".code-index").resolve()


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


def test_atlas_index_dry_run_respects_atlasignore(tmp_path):
    """`dry_run=true` exclui de candidates/total_eligible arquivos casados por `.atlasignore`."""
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.py").write_text("def main(): pass\n")
    (workspace / "src" / "helper.py").write_text("def helper(): pass\n")
    (workspace / "README.md").write_text("# Project\n")
    (workspace / ".atlasignore").write_text("README.md\nsrc/helper.py\n", encoding="utf-8")

    with patch("codesteer_atlas.server.INDEX_DIR_PATH", tmp_path / ".code-index"):
        result_json = atlas_index(workspace=str(workspace), dry_run=True)

    result = json.loads(result_json)

    paths = {c["path"]: c["eligible_files"] for c in result["candidates"]}
    assert paths.get("src") == 1
    assert "README.md" not in paths
    assert result["total_eligible_files"] == 1


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


def test_atlas_search_docstring_instructs_proactive_use():
    """Regressão de contrato: a docstring deve orientar uso proativo e citar documentos."""
    doc = (atlas_search.__doc__ or "").lower()
    # orientação de uso proativo (explorar/investigar/primeiro)
    assert any(k in doc for k in ("explore", "investigate", "first"))
    # escopo cobre documentos, não só código-fonte
    assert "document" in doc


def test_background_reindex_skips_when_no_index(tmp_path, capsys):
    """Sem índice existente, `_spawn_background_reindex` não inicia subprocesso e loga 'pulando'."""
    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", tmp_path / ".code-index"),
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=False),
        patch("codesteer_atlas.server.subprocess.Popen") as mock_popen,
    ):
        _spawn_background_reindex()

    mock_popen.assert_not_called()
    err = capsys.readouterr().err
    assert "[atlas]" in err
    assert "pulando reindex automático" in err


def test_background_reindex_runs_incremental_when_index_exists(tmp_path, capsys):
    """Com índice existente e workspace válido, inicia subprocesso de reindex incremental."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    index_dir = workspace / ".code-index"
    index_dir.mkdir()

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", index_dir),
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch("codesteer_atlas.server._index_workspace_root", return_value=workspace),
        patch("codesteer_atlas.server.subprocess.Popen") as mock_popen,
    ):
        _spawn_background_reindex()

    # Garante que o subprocesso de reindex foi iniciado com workspace e índice corretos,
    # sem stdin (não bloqueia esperando input) e sem o flag --full
    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args
    cmd = args[0]
    assert cmd[:3] == [sys.executable, "-m", "codesteer_atlas.indexer"]
    assert "--workspace" in cmd and str(workspace) in cmd
    assert "--index-dir" in cmd and str(index_dir) in cmd
    assert "--full" not in cmd
    assert kwargs["stdin"] == subprocess.DEVNULL

    err = capsys.readouterr().err
    assert "[atlas]" in err
    assert "processo separado" in err


def test_background_reindex_captures_exception_and_logs(tmp_path, capsys):
    """Exceção ao iniciar o subprocesso é capturada, logada com prefixo `[atlas]` e não propaga."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    index_dir = workspace / ".code-index"
    index_dir.mkdir()

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", index_dir),
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch("codesteer_atlas.server._index_workspace_root", return_value=workspace),
        patch("codesteer_atlas.server.subprocess.Popen", side_effect=Exception("boom")),
    ):
        # Não deve propagar
        _spawn_background_reindex()

    err = capsys.readouterr().err
    assert "[atlas] Erro ao iniciar reindex automático em background: boom" in err


def test_main_spawns_background_reindex_without_blocking(monkeypatch, tmp_path):
    """`main()` dispara o reindex em background (subprocesso, sem aguardar) e chama `app.run()`."""
    monkeypatch.setattr("sys.argv", ["atlas-serve"])

    with (
        patch("codesteer_atlas.server._spawn_background_reindex") as mock_spawn,
        patch("codesteer_atlas.server.app.run") as mock_app_run,
        patch("codesteer_atlas.server.resolve_index_dir", return_value=tmp_path / ".code-index"),
    ):
        main()

    mock_spawn.assert_called_once_with()
    mock_app_run.assert_called_once_with()


class _FakeResponder:
    def __init__(self, completed: bool):
        self._completed = completed
        self.request_id = "req-123"


def test_safe_responder_respond_ignores_late_response_when_completed(capsys):
    """Resposta tardia a uma request já cancelada/concluída é ignorada (não levanta AssertionError)."""
    responder = _FakeResponder(completed=True)

    asyncio.run(_safe_responder_respond(responder, "result"))

    err = capsys.readouterr().err
    assert "[atlas]" in err
    assert "Ignorando resposta tardia" in err
    assert "req-123" in err


def test_atlas_index_full_true_returns_async_status_without_calling_index_workspace(tmp_path):
    """`full=True` retorna status assíncrono via `_spawn_index_subprocess`,
    sem chamar `index_workspace` diretamente."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", tmp_path / ".code-index"),
        patch(
            "codesteer_atlas.server._spawn_index_subprocess",
            return_value={"status": "started", "pid": 1234, "log_path": "/tmp/log"},
        ) as mock_spawn,
        patch("codesteer_atlas.server.index_workspace") as mock_index_workspace,
    ):
        result_json = atlas_index(workspace=str(workspace), full=True, dry_run=False)

    result = json.loads(result_json)
    assert result["status"] == "started"
    assert result["pid"] == 1234
    assert "message" in result
    mock_spawn.assert_called_once()
    mock_index_workspace.assert_not_called()


def test_atlas_index_paths_none_returns_async_status_without_calling_index_workspace(tmp_path):
    """`paths=None` (com `full=False`, `dry_run=False`) retorna status assíncrono,
    sem chamar `index_workspace` diretamente."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", tmp_path / ".code-index"),
        patch(
            "codesteer_atlas.server._spawn_index_subprocess",
            return_value={"status": "skipped", "reason": "reindex_in_progress", "log_path": "/tmp/log"},
        ) as mock_spawn,
        patch("codesteer_atlas.server.index_workspace") as mock_index_workspace,
    ):
        result_json = atlas_index(workspace=str(workspace), paths=None, full=False, dry_run=False)

    result = json.loads(result_json)
    assert result["status"] == "skipped"
    assert result["reason"] == "reindex_in_progress"
    assert "message" in result
    mock_spawn.assert_called_once()
    mock_index_workspace.assert_not_called()


def test_atlas_index_paths_non_empty_full_false_remains_synchronous(tmp_path):
    """`paths=[...]` e `full=False` continua síncrono, retornando IndexStats completo."""
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)

    mock_stats = IndexStats(
        files_processed=2,
        files_skipped_unchanged=0,
        files_removed=0,
        chunks_persisted=5,
        duration_s=0.1,
        git_head_sha="sha123",
    )

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", tmp_path / ".code-index"),
        patch("codesteer_atlas.server.index_workspace", return_value=mock_stats) as mock_index,
        patch("codesteer_atlas.server._spawn_index_subprocess") as mock_spawn,
    ):
        result_json = atlas_index(workspace=str(workspace), paths=["src"], full=False, dry_run=False)

    result = json.loads(result_json)
    assert result["files_processed"] == 2
    assert result["chunks_persisted"] == 5
    assert result["git_head_sha"] == "sha123"
    assert "skipped_reason" not in result
    mock_index.assert_called_once()
    mock_spawn.assert_not_called()


def test_atlas_index_sync_propagates_skipped_reason(tmp_path):
    """Quando `index_workspace` retorna `skipped_reason`, a resposta inclui
    `skipped_reason` e `message`."""
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)

    mock_stats = IndexStats(
        files_processed=0,
        files_skipped_unchanged=0,
        files_removed=0,
        chunks_persisted=0,
        duration_s=0.0,
        git_head_sha=None,
        skipped_reason="reindex_in_progress",
    )

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", tmp_path / ".code-index"),
        patch("codesteer_atlas.server.index_workspace", return_value=mock_stats),
    ):
        result_json = atlas_index(workspace=str(workspace), paths=["src"], full=False, dry_run=False)

    result = json.loads(result_json)
    assert result["skipped_reason"] == "reindex_in_progress"
    assert "message" in result


def test_spawn_index_subprocess_skips_when_locked(tmp_path):
    """Quando `is_reindex_locked=True`, NÃO chama `subprocess.Popen` e retorna status='skipped'."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    index_dir = tmp_path / ".code-index"
    index_dir.mkdir()

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", index_dir),
        patch("codesteer_atlas.server.is_reindex_locked", return_value=True),
        patch("codesteer_atlas.server.subprocess.Popen") as mock_popen,
    ):
        result = _spawn_index_subprocess(workspace, paths=None, full=False)

    assert result["status"] == "skipped"
    assert result["reason"] == "reindex_in_progress"
    mock_popen.assert_not_called()


def test_spawn_index_subprocess_started_with_full_and_paths(tmp_path):
    """Quando `is_reindex_locked=False`, chama `subprocess.Popen` com
    --workspace/--index-dir/--full/--paths e retorna status='started' com pid."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    index_dir = tmp_path / ".code-index"
    index_dir.mkdir()

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", index_dir),
        patch("codesteer_atlas.server.is_reindex_locked", return_value=False),
        patch("codesteer_atlas.server.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4242
        result = _spawn_index_subprocess(workspace, paths=["src", "docs"], full=True)

    assert result["status"] == "started"
    assert result["pid"] == 4242

    args, kwargs = mock_popen.call_args
    cmd = args[0]
    assert cmd[:3] == [sys.executable, "-m", "codesteer_atlas.indexer"]
    assert "--workspace" in cmd and str(workspace) in cmd
    assert "--index-dir" in cmd and str(index_dir) in cmd
    assert "--full" in cmd
    assert cmd.count("--paths") == 2
    assert "src" in cmd and "docs" in cmd
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_spawn_index_subprocess_popen_raises_returns_error(tmp_path):
    """Quando `Popen` lança exceção, retorna status='error' com a mensagem."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    index_dir = tmp_path / ".code-index"
    index_dir.mkdir()

    with (
        patch("codesteer_atlas.server.INDEX_DIR_PATH", index_dir),
        patch("codesteer_atlas.server.is_reindex_locked", return_value=False),
        patch("codesteer_atlas.server.subprocess.Popen", side_effect=Exception("boom")),
    ):
        result = _spawn_index_subprocess(workspace, paths=None, full=False)

    assert result["status"] == "error"
    assert "boom" in result["error"]


def test_atlas_status_reindexing_true_when_locked():
    """`atlas_status` retorna `reindexing=True` quando `is_reindex_locked` retorna True."""
    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=False),
        patch("codesteer_atlas.server.is_reindex_locked", return_value=True),
    ):
        result = json.loads(atlas_status())

    assert result["reindexing"] is True


def test_atlas_status_reindexing_false_when_unlocked():
    """`atlas_status` retorna `reindexing=False` quando `is_reindex_locked` retorna False."""
    with (
        patch("codesteer_atlas.storage.StorageBackend.exists", return_value=True),
        patch("codesteer_atlas.storage.StorageBackend.get_manifest", return_value=MOCK_MANIFEST),
        patch("codesteer_atlas.server.get_git_head_sha", return_value="sha_98765"),
        patch("codesteer_atlas.server.is_reindex_locked", return_value=False),
    ):
        result = json.loads(atlas_status())

    assert result["reindexing"] is False


def test_safe_responder_respond_delegates_when_not_completed():
    """Quando a request ainda não foi concluída, delega normalmente para `respond()` original."""
    responder = _FakeResponder(completed=False)
    calls = []

    async def fake_original(self, response):
        calls.append((self, response))

    with patch("codesteer_atlas.server._original_responder_respond", fake_original):
        asyncio.run(_safe_responder_respond(responder, "result"))

    assert calls == [(responder, "result")]


def test_atlas_status_includes_index_resolution_source():
    """`atlas_status` expõe `index_resolution` com a origem registrada pela última
    chamada de `resolve_index_dir` (autodiagnóstico de configuração do cliente)."""
    import codesteer_atlas.server as server_module

    original_source = server_module.INDEX_RESOLUTION_SOURCE
    try:
        resolve_index_dir(cli_arg=None, env={"ATLAS_INDEX_DIR": "/tmp/fake-index"})
        with (
            patch("codesteer_atlas.storage.StorageBackend.exists", return_value=False),
            patch("codesteer_atlas.server.is_reindex_locked", return_value=False),
        ):
            result = json.loads(atlas_status())

        assert result["index_resolution"] == "env"
    finally:
        server_module.INDEX_RESOLUTION_SOURCE = original_source


def test_resolve_index_dir_records_resolution_source(tmp_path):
    """`resolve_index_dir` registra a origem usada em `INDEX_RESOLUTION_SOURCE`
    para cada mecanismo da cadeia DECISAO-002."""
    import codesteer_atlas.server as server_module

    original_source = server_module.INDEX_RESOLUTION_SOURCE
    try:
        resolve_index_dir(cli_arg=str(tmp_path / "cli"))
        assert server_module.INDEX_RESOLUTION_SOURCE == "cli-arg"

        resolve_index_dir(cli_arg=None, env={"ATLAS_INDEX_DIR": str(tmp_path / "env")})
        assert server_module.INDEX_RESOLUTION_SOURCE == "env"

        # Fallback é verificado antes de criar `.code-index`, para a discovery
        # ascendente a partir de tmp_path não encontrar nada
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        resolve_index_dir(cli_arg=None, env={}, start_dir=isolated)
        assert server_module.INDEX_RESOLUTION_SOURCE == "cwd-fallback"

        (tmp_path / ".code-index").mkdir()
        sub_dir = tmp_path / "a" / "b"
        sub_dir.mkdir(parents=True)
        resolve_index_dir(cli_arg=None, env={}, start_dir=sub_dir)
        assert server_module.INDEX_RESOLUTION_SOURCE == "discovery"
    finally:
        server_module.INDEX_RESOLUTION_SOURCE = original_source
