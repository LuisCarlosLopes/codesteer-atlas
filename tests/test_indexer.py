import json
from unittest.mock import patch
from click.testing import CliRunner
from codesteer_atlas.indexer import cli, index_workspace, should_ignore


def test_should_ignore_rules(tmp_path):
    """
    Testa se a função de ignore do indexador detecta corretamente
    pastas e arquivos que devem ser ignorados.
    """
    # 1. Pastas do IGNORE_DIRS
    assert should_ignore(tmp_path / ".git", tmp_path) is True
    assert should_ignore(tmp_path / "node_modules", tmp_path) is True
    assert should_ignore(tmp_path / "src" / "node_modules" / "utils.js", tmp_path) is True

    # 2. Arquivos normais não devem ser ignorados
    assert should_ignore(tmp_path / "src" / "main.py", tmp_path) is False
    assert should_ignore(tmp_path / "utils.go", tmp_path) is False


def test_indexer_cli_run(tmp_path):
    """
    Testa a execução de ponta a ponta da CLI do indexador de forma mockada,
    validando o fluxo de escaneamento, geração de manifest e persistência.
    """
    runner = CliRunner()

    # Cria a estrutura do workspace de teste
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    # Arquivo Python válido
    file1 = workspace_dir / "app.py"
    file1.write_text("def run_app():\n    print('app running')\n", encoding="utf-8")

    # Arquivo JS válido
    file2 = workspace_dir / "index.js"
    file2.write_text("function init() {}\n", encoding="utf-8")

    # Arquivo de extensão não suportada (deve ser ignorado)
    file3 = workspace_dir / "docs.log"
    file3.write_text("Hello docs\n", encoding="utf-8")

    # Arquivo muito grande > 2MB (deve ser ignorado)
    file4 = workspace_dir / "large.py"
    file4.write_text("x = 1\n" * 500000, encoding="utf-8")  # ~3MB

    index_dir = tmp_path / "index_output"

    # Mock do EmbeddingEngine.encode para retornar vetores estáticos falsos
    # de tamanho 384 sem inicializar o modelo de verdade
    mock_vectors = [[0.1] * 384, [0.2] * 384]

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", return_value=mock_vectors
        ) as mock_encode,
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_12345"),
    ):
        result = runner.invoke(
            cli, ["--workspace", str(workspace_dir), "--index-dir", str(index_dir)]
        )

        # Verifica se a execução foi bem-sucedida
        assert result.exit_code == 0
        assert "Indexação Concluída com Sucesso!" in result.output
        assert (
            "Total de chunks persistidos: 2" in result.output
        )  # app.py (run_app) e index.js (init)
        assert "Arquivos ignorados (> 2MB): 1" in result.output

        # Verifica se o manifest e banco foram criados
        manifest_file = index_dir / "manifest.json"
        assert manifest_file.exists()

        # Garante que o encode em lote foi chamado
        mock_encode.assert_called_once()


def _patched_encode(texts, batch_size=32):
    """Mock determinístico de embeddings: um vetor [0.1]*384 por texto."""
    return [[0.1] * 384 for _ in texts]


def test_index_workspace_second_run_skips_unchanged_files(tmp_path):
    """2ª execução sem mudanças: 0 embeddings gerados (todos os arquivos inalterados)."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    (workspace_dir / "app.py").write_text("def run_app():\n    pass\n", encoding="utf-8")
    (workspace_dir / "utils.py").write_text("def helper():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ) as mock_encode,
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        # Primeira execução: indexa tudo
        stats1 = index_workspace(workspace_dir, index_dir)
        assert stats1.files_processed == 2
        assert stats1.files_skipped_unchanged == 0
        assert mock_encode.call_count == 1

        # Segunda execução: nada mudou
        mock_encode.reset_mock()
        stats2 = index_workspace(workspace_dir, index_dir)
        assert stats2.files_processed == 0
        assert stats2.files_skipped_unchanged == 2
        assert stats2.files_removed == 0
        # Nenhum embedding deve ser gerado (lista de chunks novos vazia)
        mock_encode.assert_not_called()


def test_index_workspace_deleted_file_removed_from_index(tmp_path):
    """Arquivo deletado é removido do índice na execução seguinte."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    file_a = workspace_dir / "a.py"
    file_b = workspace_dir / "b.py"
    file_a.write_text("def a():\n    pass\n", encoding="utf-8")
    file_b.write_text("def b():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        stats1 = index_workspace(workspace_dir, index_dir)
        assert stats1.files_processed == 2

        # Remove b.py e reindexa
        file_b.unlink()
        stats2 = index_workspace(workspace_dir, index_dir)

        assert stats2.files_removed == 1

        with open(index_dir / "manifest.json", encoding="utf-8") as f:
            manifest = json.load(f)

        assert "b.py" not in manifest["files"]
        assert "a.py" in manifest["files"]


def test_index_workspace_full_flag_rebuilds_everything(tmp_path):
    """`--full` (full=True) reconstrói tudo, ignorando os hashes do manifest."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    (workspace_dir / "app.py").write_text("def run_app():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ) as mock_encode,
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        stats1 = index_workspace(workspace_dir, index_dir)
        assert stats1.files_processed == 1

        # Nada mudou, mas full=True força reprocessamento
        mock_encode.reset_mock()
        stats2 = index_workspace(workspace_dir, index_dir, full=True)

        assert stats2.files_processed == 1
        assert stats2.files_skipped_unchanged == 0
        mock_encode.assert_called_once()


def test_index_workspace_file_path_always_posix(tmp_path):
    """`file_path` persistido no manifest é sempre POSIX (sem separadores '\\\\')."""
    workspace_dir = tmp_path / "workspace"
    nested_dir = workspace_dir / "src" / "controllers"
    nested_dir.mkdir(parents=True)

    (nested_dir / "user.py").write_text("def handler():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        index_workspace(workspace_dir, index_dir)

    with open(index_dir / "manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "src/controllers/user.py" in manifest["files"]
    assert all("\\" not in path for path in manifest["files"].keys())


def test_index_workspace_partial_paths_preserves_other_folders(tmp_path):
    """`paths=["src"]` só processa a subárvore selecionada e preserva chunks de outras pastas."""
    workspace_dir = tmp_path / "workspace"
    src_dir = workspace_dir / "src"
    docs_dir = workspace_dir / "docs"
    src_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)

    (src_dir / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (docs_dir / "guide.md").write_text("# Guide\n\nSome content here.\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        # Indexação completa inicial
        index_workspace(workspace_dir, index_dir)

        with open(index_dir / "manifest.json", encoding="utf-8") as f:
            manifest_before = json.load(f)
        assert "src/main.py" in manifest_before["files"]
        assert "docs/guide.md" in manifest_before["files"]

        # Altera apenas src/main.py e reindexa só "src"
        (src_dir / "main.py").write_text("def main():\n    print('changed')\n", encoding="utf-8")

        stats = index_workspace(workspace_dir, index_dir, paths=["src"])

        assert stats.files_processed == 1

        with open(index_dir / "manifest.json", encoding="utf-8") as f:
            manifest_after = json.load(f)

        # docs/guide.md deve permanecer no manifest, intocado
        assert "docs/guide.md" in manifest_after["files"]
        assert manifest_after["files"]["docs/guide.md"] == manifest_before["files"]["docs/guide.md"]
        assert "src/main.py" in manifest_after["files"]
