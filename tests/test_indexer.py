import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pytest
from click.testing import CliRunner
from filelock import FileLock

from codesteer_atlas.config import REINDEX_LOCK_FILENAME
from codesteer_atlas.indexer import (
    cli,
    get_git_head_sha,
    index_workspace,
    load_atlasignore_spec,
    should_ignore,
)
from codesteer_atlas.origin import OriginResult
from codesteer_atlas.storage import StorageBackend


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


def test_indexer_off_para_incremental_on_preserva_schema_e_chunks(tmp_path, monkeypatch):
    """A primeira gravação off aceita propósito na atualização incremental seguinte."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (workspace / "other.py").write_text("def keep():\n    return 2\n", encoding="utf-8")
    index_dir = tmp_path / "index"

    class SamplingResult:
        text = "propósito gerado"
        result = "propósito gerado"

    class FakeContext:
        def __init__(self):
            self.calls = []

        def sample(self, **kwargs):
            self.calls.append(kwargs)
            return SamplingResult()

    monkeypatch.delenv("ATLAS_SEMANTIC", raising=False)
    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
    ):
        off_stats = index_workspace(workspace, index_dir, report_progress=False)

    table = StorageBackend(index_dir).db_path
    import lancedb

    schema = lancedb.connect(str(table)).open_table("chunks").schema
    assert schema.field("purpose").type == pa.string()
    assert schema.field("purpose").nullable is True
    assert schema.field("purpose_vector").nullable is True
    assert off_stats.semantic_status == "disabled"

    (workspace / "app.py").write_text("def run():\n    return 10\n", encoding="utf-8")
    context = FakeContext()
    monkeypatch.setenv("ATLAS_SEMANTIC", "1")
    observed_paths = []
    original_cache = StorageBackend.get_semantic_cache

    def spy_cache(storage, file_paths=None):
        observed_paths.append(file_paths)
        return original_cache(storage, file_paths)

    monkeypatch.setattr(StorageBackend, "get_semantic_cache", spy_cache)
    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-2"),
    ):
        on_stats = index_workspace(workspace, index_dir, report_progress=False, ctx=context)

    assert on_stats.semantic_generated >= 1
    assert context.calls
    assert observed_paths == [["app.py"]]
    storage = StorageBackend(index_dir)
    rows = lancedb.connect(str(storage.db_path)).open_table("chunks").to_arrow().to_pylist()
    by_file = {row["file_path"]: row for row in rows}
    assert by_file["app.py"]["purpose"] == "propósito gerado"
    assert by_file["other.py"]["purpose"] is None
    sidecar = json.loads((index_dir / "semantic.json").read_text(encoding="utf-8"))
    assert sidecar["usable_purpose_count"] >= 1


def test_indexer_full_on_fecha_t4_e_preserva_artefatos_estruturais(tmp_path, monkeypatch):
    """O caminho integrado on gera prosa elegível sem contaminar documentos ou grafo."""
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (workspace / "README.md").write_text("# Guia\n\nUso do projeto.\n", encoding="utf-8")
    index_dir = tmp_path / "index"

    def generate(_self, _payload):
        return OriginResult("responsabilidade do símbolo", "local", "host local")

    monkeypatch.setenv("ATLAS_SEMANTIC", "1")
    monkeypatch.setenv("ATLAS_SEMANTIC_LOCAL_URL", "http://local.test")
    monkeypatch.setattr("codesteer_atlas.origin.OriginResolver.generate", generate)
    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
    ):
        stats = index_workspace(workspace, index_dir, full=True, report_progress=False)

    import lancedb

    rows = lancedb.connect(str(index_dir / "lancedb")).open_table("chunks").to_arrow().to_pylist()
    assert stats.semantic_status == "ok"
    assert stats.semantic_file_generated >= 1
    assert any(row["purpose"] == "responsabilidade do símbolo" for row in rows)
    assert all(row["purpose"] is None for row in rows if row["language"] == "markdown")
    assert (index_dir / "semantic.json").exists()
    assert (index_dir / "graph.json").exists()
    assert (index_dir / "graph.html").exists()
    assert (index_dir / "brief.json").exists()

    graph = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
    brief = json.loads((index_dir / "brief.json").read_text(encoding="utf-8"))
    graph_html = (index_dir / "graph.html").read_text(encoding="utf-8")
    for structural_artifact in (graph, brief):
        serialized = json.dumps(structural_artifact, ensure_ascii=False)
        assert "responsabilidade do símbolo" not in serialized
        assert "file_summaries" not in serialized
        assert "layer_summaries" not in serialized
    assert graph["nodes"]
    assert "responsabilidade do símbolo" not in graph_html
    assert "file_summaries" not in graph_html
    assert brief["layers"]


def test_indexer_matriz_legado_rejeita_mutacao_e_full_integral_converte(
    tmp_path, monkeypatch
):
    """T4/T8: só o full integral converte legado, após três rejeições imutáveis."""
    import lancedb

    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "app.py").write_text(
        "def run():\n    return 1\n", encoding="utf-8"
    )
    (workspace / "src" / "sibling.py").write_text(
        "def keep():\n    return 2\n", encoding="utf-8"
    )
    index_dir = tmp_path / "index"
    monkeypatch.delenv("ATLAS_SEMANTIC", raising=False)
    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
    ):
        index_workspace(workspace, index_dir, report_progress=False)

    manifest_path = index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["index_version"] = "2.2.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    db = lancedb.connect(str(index_dir / "lancedb"))
    current_rows = db.open_table("chunks").to_arrow().to_pylist()
    legacy_rows = [
        {
            key: value
            for key, value in row.items()
            if key not in {"purpose", "purpose_hash", "purpose_vector"}
        }
        for row in current_rows
    ]
    db.drop_table("chunks")
    db.create_table("chunks", data=legacy_rows).create_fts_index("content", replace=True)
    (workspace / "src" / "app.py").write_text(
        "def run():\n    return 100\n", encoding="utf-8"
    )

    table = lancedb.connect(str(index_dir / "lancedb")).open_table("chunks")
    rows_before = table.to_arrow().to_pylist()
    manifest_before = manifest_path.read_bytes()
    sidecar_before = (index_dir / "semantic.json").read_bytes()

    rejected_modes = [
        (False, False, None),
        (True, False, None),
        (True, True, ["src"]),
    ]
    for semantic_on, full, paths in rejected_modes:
        if semantic_on:
            monkeypatch.setenv("ATLAS_SEMANTIC", "1")
        else:
            monkeypatch.delenv("ATLAS_SEMANTIC", raising=False)
        with pytest.raises(RuntimeError, match="legado.*full=true sem paths"):
            index_workspace(
                workspace,
                index_dir,
                paths=paths,
                full=full,
                report_progress=False,
            )
        assert manifest_path.read_bytes() == manifest_before
        assert (index_dir / "semantic.json").read_bytes() == sidecar_before
        rows_after = (
            lancedb.connect(str(index_dir / "lancedb"))
            .open_table("chunks")
            .to_arrow()
            .to_pylist()
        )
        assert rows_after == rows_before

    def generate(_self, payload):
        return OriginResult(
            f"purpose:{payload['scope_type']}:{payload['scope_name']}", "local", "host local"
        )

    monkeypatch.setenv("ATLAS_SEMANTIC", "1")
    monkeypatch.setenv("ATLAS_SEMANTIC_LOCAL_URL", "http://local.test")
    monkeypatch.setattr("codesteer_atlas.origin.OriginResolver.generate", generate)
    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-2"),
    ):
        converted = index_workspace(
            workspace, index_dir, full=True, paths=None, report_progress=False
        )

    converted_manifest = StorageBackend(index_dir).get_manifest()
    converted_rows = (
        lancedb.connect(str(index_dir / "lancedb"))
        .open_table("chunks")
        .to_arrow()
        .to_pylist()
    )
    assert converted_manifest.index_version == "2.3.0"
    assert converted.semantic_status == "ok"
    assert any("return 100" in row["content"] for row in converted_rows)
    assert all(row["purpose"] for row in converted_rows)
    assert all(row["purpose_vector"] for row in converted_rows)


def test_indexer_incremental_reusa_irmao_pre_delete_e_atualiza_contadores(
    tmp_path, monkeypatch
):
    """Cache é lido antes do delete e preserva o irmão não alterado do mesmo arquivo."""
    import lancedb

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "app.py"
    source.write_text(
        "def changed():\n    return 1\n\ndef stable():\n    return 2\n", encoding="utf-8"
    )
    index_dir = tmp_path / "index"

    def generate(_self, payload):
        if payload["scope_type"] in {"arquivo", "camada"}:
            text = f"summary:{payload['scope_type']}:{payload['scope_name']}:{payload['content']}"
        else:
            text = f"purpose:{payload['scope_name']}:{payload['content']}"
        return OriginResult(text, "local", "host local")

    monkeypatch.setenv("ATLAS_SEMANTIC", "1")
    monkeypatch.setenv("ATLAS_SEMANTIC_LOCAL_URL", "http://local.test")
    monkeypatch.setattr("codesteer_atlas.origin.OriginResolver.generate", generate)
    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
    ):
        first = index_workspace(workspace, index_dir, full=True, report_progress=False)

    first_rows = (
        lancedb.connect(str(index_dir / "lancedb"))
        .open_table("chunks")
        .to_arrow()
        .to_pylist()
    )
    first_by_name = {row["scope_name"]: row for row in first_rows}
    assert first.semantic_generated == 2

    source.write_text(
        "def changed():\n    return 100\n\ndef stable():\n    return 2\n", encoding="utf-8"
    )
    observed_cache_paths = []
    original_cache = StorageBackend.get_semantic_cache

    def spy_cache(storage, file_paths=None):
        observed_cache_paths.append(file_paths)
        return original_cache(storage, file_paths)

    monkeypatch.setattr(StorageBackend, "get_semantic_cache", spy_cache)
    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-2"),
    ):
        second = index_workspace(workspace, index_dir, report_progress=False)

    second_rows = (
        lancedb.connect(str(index_dir / "lancedb"))
        .open_table("chunks")
        .to_arrow()
        .to_pylist()
    )
    second_by_name = {row["scope_name"]: row for row in second_rows}
    assert observed_cache_paths == [["app.py"]]
    assert second.semantic_generated == 1
    assert second.semantic_reused == 1
    assert second_by_name["stable"]["purpose"] == first_by_name["stable"]["purpose"]
    assert second_by_name["stable"]["purpose_vector"] == first_by_name["stable"]["purpose_vector"]
    assert second_by_name["changed"]["purpose"] != first_by_name["changed"]["purpose"]

    sidecar = json.loads((index_dir / "semantic.json").read_text(encoding="utf-8"))
    last = sidecar["last_generation"]
    assert sidecar["usable_purpose_count"] == 2
    assert last["status"] == "ok"
    assert last["semantic_generated"] == 1
    assert last["semantic_reused"] == 1
    assert last["semantic_file_generated"] == 1
    assert last["semantic_layer_generated"] == 1
    assert last["origins"] == ["local"]
    assert last["egresses"] == ["host local"]


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_origin"),
    [("v05", "failed", "local"), ("offline", "no_origin", None)],
)
def test_indexer_v05_e_offline_degradam_sem_host_api(
    tmp_path, monkeypatch, mode, expected_status, expected_origin
):
    """V05 e ausência de origem mantêm T8 e não tentam host/API implícitos."""
    import lancedb

    workspace = tmp_path / mode / "workspace"
    workspace.mkdir(parents=True)
    source = "def run():\n    return 1\n"
    (workspace / "app.py").write_text(source, encoding="utf-8")
    index_dir = tmp_path / mode / "index"
    monkeypatch.setenv("ATLAS_SEMANTIC", "1")
    monkeypatch.delenv("ATLAS_SEMANTIC_LOCAL_URL", raising=False)
    monkeypatch.delenv("ATLAS_SEMANTIC_API_URL", raising=False)
    monkeypatch.delenv("ATLAS_SEMANTIC_API_KEY", raising=False)

    with patch("codesteer_atlas.origin.OriginResolver._call_http") as http:
        if mode == "v05":
            monkeypatch.setenv("ATLAS_SEMANTIC_LOCAL_URL", "http://local.test")
            monkeypatch.setattr(
                "codesteer_atlas.origin.OriginResolver.generate",
                lambda _self, _payload: OriginResult("  \n", "local", "host local"),
            )
        with (
            patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
            patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha"),
        ):
            stats = index_workspace(workspace, index_dir, full=True, report_progress=False)

    http.assert_not_called()
    sidecar = json.loads((index_dir / "semantic.json").read_text(encoding="utf-8"))
    assert stats.semantic_status == expected_status
    assert sidecar["usable_purpose_count"] == 0
    assert sidecar["last_generation"]["status"] == expected_status
    assert sidecar["origin"] == expected_origin

    storage = StorageBackend(index_dir)
    manifest = storage.get_manifest()
    table = lancedb.connect(str(storage.db_path)).open_table("chunks")
    rows = table.to_arrow().to_pylist()
    assert len(rows) == manifest.total_chunks == stats.chunks_persisted == 1
    assert table.schema.field("purpose").type == pa.string()
    assert table.schema.field("purpose").nullable is True
    assert table.schema.field("purpose_hash").nullable is True
    assert table.schema.field("purpose_vector").type == pa.list_(pa.float32(), 384)
    assert table.schema.field("purpose_vector").nullable is True

    row = rows[0]
    assert row["file_path"] == "app.py"
    assert row["scope_type"] == "function"
    assert row["scope_name"] == "run"
    assert row["language"] == "python"
    assert row["content"] == source.rstrip()
    assert row["vector"] == pytest.approx([0.1] * 384)
    assert row["purpose"] is None
    assert row["purpose_hash"] is None
    assert row["purpose_vector"] is None

    assert manifest.index_version == "2.3.0"
    assert manifest.repos_indexed == ["workspace"]
    assert manifest.languages_indexed == ["python"]
    assert manifest.embedding_dim == 384
    assert manifest.embedding_backend == "fastembed"
    assert manifest.storage_backend == "lancedb"
    assert manifest.git_head_sha == "sha"
    assert manifest.files == {
        "app.py": hashlib.sha256(source.encode("utf-8")).hexdigest()
    }
    assert set(manifest.files_meta) == {"app.py"}
    assert manifest.files_meta["app.py"][1] == len(source.encode("utf-8"))
    assert (index_dir / "graph.json").exists()
    assert (index_dir / "graph.html").exists()
    assert (index_dir / "brief.json").exists()


def _patched_encode(texts, batch_size=32, on_progress=None):
    """Mock determinístico de embeddings: um vetor [0.1]*384 por texto."""
    total = len(texts)
    if on_progress is not None and total:
        on_progress(total, total)
    return [[0.1] * 384 for _ in texts]


def test_index_progress_reporter_emits_phases(tmp_path, capsys):
    """Progresso por fase é emitido em stderr e só atinge 100% ao finalizar."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "app.py").write_text("def run_app():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        index_workspace(workspace_dir, index_dir, report_progress=True)

    captured = capsys.readouterr()
    assert "[atlas]" in captured.err
    assert "Varredura do workspace" in captured.err
    assert "Persistindo no LanceDB" in captured.err
    assert captured.err.strip().endswith("[atlas] 100% — Indexação concluída")


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
        assert stats2.graph_strategy == "skipped-unchanged"
        # Nenhum embedding deve ser gerado (lista de chunks novos vazia)
        mock_encode.assert_not_called()


def test_index_workspace_reports_metrics_and_incremental_graph_strategy(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    app_file = workspace_dir / "app.py"
    app_file.write_text("def run_app():\n    return 1\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        first = index_workspace(workspace_dir, index_dir)
        app_file.write_text("def run_app():\n    return 2\n", encoding="utf-8")
        second = index_workspace(workspace_dir, index_dir)

    assert first.files_scanned == 1
    assert first.files_eligible == 1
    assert first.chunks_generated >= 1
    assert set(first.phase_durations_s) == {
        "scan",
        "hash",
        "chunk",
        "embed",
        "persist",
        "graph",
        "brief",
    }
    assert first.graph_nodes > 0
    assert first.graph_bytes > 0
    assert second.graph_strategy == "incremental-code"


def test_index_workspace_unchanged_files_skip_hashing_via_mtime_size(tmp_path):
    """2ª execução sem mudanças: o conteúdo dos arquivos não é relido/hasheado
    (fast path por mtime+size [P01]), mas o hash persistido continua o mesmo."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    (workspace_dir / "app.py").write_text("def run_app():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        stats1 = index_workspace(workspace_dir, index_dir)
        assert stats1.files_processed == 1

        manifest_path = index_dir / "manifest.json"
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "app.py" in manifest_data["files_meta"]
        original_hash = manifest_data["files"]["app.py"]

        # Segunda execução: nada mudou (mesmo mtime/size) — não deve reler o conteúdo
        with patch(
            "codesteer_atlas.indexer._hash_file_content"
        ) as mock_hash_content:
            stats2 = index_workspace(workspace_dir, index_dir)

        mock_hash_content.assert_not_called()
        assert stats2.files_processed == 0
        assert stats2.files_skipped_unchanged == 1

        manifest_data2 = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest_data2["files"]["app.py"] == original_hash


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


def test_load_atlasignore_spec_returns_none_when_file_absent(tmp_path):
    """Sem `.atlasignore` na raiz, retorna None (comportamento atual preservado)."""
    assert load_atlasignore_spec(tmp_path) is None


def test_load_atlasignore_spec_returns_pathspec_when_file_present(tmp_path):
    """Com `.atlasignore` presente, retorna um PathSpec ignorando comentários/linhas em branco."""
    (tmp_path / ".atlasignore").write_text(
        "# comentário\n\n*.log\n\nbuild/\n", encoding="utf-8"
    )

    spec = load_atlasignore_spec(tmp_path)

    assert spec is not None
    assert spec.match_file("debug.log") is True
    assert spec.match_file("src/main.py") is False


def test_load_atlasignore_spec_returns_none_when_unreadable(tmp_path):
    """`.atlasignore` ilegível (ex.: é um diretório) é tratado como ausente."""
    (tmp_path / ".atlasignore").mkdir()

    assert load_atlasignore_spec(tmp_path) is None


def test_should_ignore_atlas_spec_simple_glob(tmp_path):
    """Padrão glob simples (`*.log`) ignora arquivos correspondentes em qualquer pasta."""
    spec = load_atlasignore_spec_from_text(tmp_path, "*.log\n")

    assert should_ignore(tmp_path / "debug.log", tmp_path, spec) is True
    assert should_ignore(tmp_path / "src" / "debug.log", tmp_path, spec) is True
    assert should_ignore(tmp_path / "main.py", tmp_path, spec) is False


def test_should_ignore_atlas_spec_directory_pattern(tmp_path):
    """Padrão de diretório (`pasta/`) ignora a árvore inteira, incl. arquivos dentro."""
    spec = load_atlasignore_spec_from_text(tmp_path, "fixtures/\n")

    (tmp_path / "fixtures").mkdir()
    (tmp_path / "src" / "fixtures").mkdir(parents=True)

    assert should_ignore(tmp_path / "fixtures", tmp_path, spec) is True
    assert should_ignore(tmp_path / "fixtures" / "data.json", tmp_path, spec) is True
    assert should_ignore(tmp_path / "src" / "fixtures", tmp_path, spec) is True


def test_should_ignore_atlas_spec_anchored_pattern(tmp_path):
    """Padrão ancorado (`/output`) só casa na raiz do workspace, não em subpastas."""
    spec = load_atlasignore_spec_from_text(tmp_path, "/output\n")

    (tmp_path / "output").mkdir()
    (tmp_path / "src" / "output").mkdir(parents=True)

    assert should_ignore(tmp_path / "output", tmp_path, spec) is True
    assert should_ignore(tmp_path / "src" / "output", tmp_path, spec) is False


def test_should_ignore_atlas_spec_double_star(tmp_path):
    """Padrão com `**` (`**/*.generated.py`) funciona em qualquer profundidade."""
    spec = load_atlasignore_spec_from_text(tmp_path, "**/*.generated.py\n")

    assert should_ignore(tmp_path / "models.generated.py", tmp_path, spec) is True
    assert should_ignore(tmp_path / "a" / "b" / "c.generated.py", tmp_path, spec) is True
    assert should_ignore(tmp_path / "models.py", tmp_path, spec) is False


def test_should_ignore_atlas_spec_negation(tmp_path):
    """Negação (`!manter.log` após `*.log`) reinclui o arquivo previamente ignorado."""
    spec = load_atlasignore_spec_from_text(tmp_path, "*.log\n!manter.log\n")

    assert should_ignore(tmp_path / "debug.log", tmp_path, spec) is True
    assert should_ignore(tmp_path / "manter.log", tmp_path, spec) is False


def test_should_ignore_atlas_spec_cannot_unignore_ignore_dirs(tmp_path):
    """`IGNORE_DIRS` (.git) continua ignorado mesmo se `.atlasignore` tentar negar."""
    spec = load_atlasignore_spec_from_text(tmp_path, "!.git\n!.git/**\n")

    assert should_ignore(tmp_path / ".git", tmp_path, spec) is True
    assert should_ignore(tmp_path / ".git" / "config", tmp_path, spec) is True


def test_should_ignore_without_atlas_spec_is_unchanged(tmp_path):
    """Sem `atlas_spec` (None), `should_ignore` mantém o comportamento de regressão."""
    assert should_ignore(tmp_path / ".git", tmp_path) is True
    assert should_ignore(tmp_path / "src" / "main.py", tmp_path) is False


def load_atlasignore_spec_from_text(tmp_path, content: str):
    """Helper: cria `.atlasignore` com `content` e retorna o PathSpec carregado."""
    (tmp_path / ".atlasignore").write_text(content, encoding="utf-8")
    return load_atlasignore_spec(tmp_path)


def test_index_workspace_respects_atlasignore(tmp_path):
    """Arquivos casados por `.atlasignore` não entram no manifest nem geram chunks."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    (workspace_dir / "app.py").write_text("def run_app():\n    pass\n", encoding="utf-8")
    (workspace_dir / "ignored.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (workspace_dir / ".atlasignore").write_text("ignored.py\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        stats = index_workspace(workspace_dir, index_dir)

    assert stats.files_processed == 1

    with open(index_dir / "manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "app.py" in manifest["files"]
    assert "ignored.py" not in manifest["files"]


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


def test_index_workspace_skips_when_lock_held_externally(tmp_path):
    """Com `.reindex.lock` já detido externamente, retorna IndexStats com
    `skipped_reason="reindex_in_progress"`, sem alterar manifest/tabela."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "app.py").write_text("def run_app():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    # Primeira execução normal, gera manifest/tabela
    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        stats1 = index_workspace(workspace_dir, index_dir)
        assert stats1.skipped_reason is None

    storage = StorageBackend(index_dir=index_dir)
    manifest_before = storage.get_manifest().model_dump()

    external_lock = FileLock(str(index_dir / REINDEX_LOCK_FILENAME), timeout=0)
    external_lock.acquire()
    try:
        with patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ) as mock_encode:
            stats2 = index_workspace(workspace_dir, index_dir)
    finally:
        external_lock.release()

    assert stats2.skipped_reason == "reindex_in_progress"
    assert stats2.files_processed == 0
    assert stats2.files_skipped_unchanged == 0
    assert stats2.files_removed == 0
    assert stats2.chunks_persisted == 0
    assert stats2.duration_s == 0.0
    assert stats2.git_head_sha is None
    mock_encode.assert_not_called()

    manifest_after = storage.get_manifest().model_dump()
    assert manifest_after == manifest_before


def test_index_workspace_normal_call_unaffected_by_lock_module(tmp_path):
    """Sem lock concorrente, `index_workspace` continua produzindo IndexStats
    completo (regressão), com `skipped_reason=None`."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "app.py").write_text("def run_app():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        stats = index_workspace(workspace_dir, index_dir)

    assert stats.skipped_reason is None
    assert stats.files_processed == 1
    assert stats.chunks_persisted >= 1
    assert stats.git_head_sha == "git_sha_1"


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
    assert all("\\" not in path for path in manifest["files"])


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

        # Chunks de outras pastas devem permanecer no LanceDB (não só no manifest)
        storage = StorageBackend(index_dir)
        symbols_after = storage.get_symbols()
        file_paths_after = {row["file_path"] for row in symbols_after}
        assert "docs/guide.md" in file_paths_after
        assert len(symbols_after) >= 2


def test_index_workspace_partial_paths_preserves_lancedb_chunk_count(tmp_path):
    """Indexação parcial incremental não deve sobrescrever chunks fora do escopo de `paths`."""
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
        index_workspace(workspace_dir, index_dir)
        storage = StorageBackend(index_dir)
        chunks_before = len(storage.get_symbols())
        assert chunks_before >= 2

        (src_dir / "main.py").write_text("def main():\n    print('changed')\n", encoding="utf-8")
        stats = index_workspace(workspace_dir, index_dir, paths=["src"])

        assert stats.files_processed == 1
        chunks_after = len(storage.get_symbols())
        assert chunks_after == chunks_before
        assert {row["file_path"] for row in storage.get_symbols()} >= {
            "src/main.py",
            "docs/guide.md",
        }


def test_get_git_head_sha_returns_none_and_logs_on_oserror(monkeypatch, tmp_path, capsys):
    """`get_git_head_sha` retorna None e loga em stderr em falhas inesperadas do
    subprocess (ex.: git fora do PATH, handle inválido no Windows), em vez de
    engolir o erro silenciosamente."""

    def raise_oserror(*args, **kwargs):
        raise OSError("The handle is invalid")

    monkeypatch.setattr("subprocess.run", raise_oserror)

    assert get_git_head_sha(tmp_path) is None
    assert "git rev-parse HEAD falhou" in capsys.readouterr().err


def test_get_git_head_sha_silent_none_when_not_a_git_repo(monkeypatch, tmp_path, capsys):
    """Fora de um repositório git (CalledProcessError), retorna None sem log —
    cenário esperado, não é falha de ambiente."""
    import subprocess

    def raise_called_process_error(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"])

    monkeypatch.setattr("subprocess.run", raise_called_process_error)

    assert get_git_head_sha(tmp_path) is None
    assert capsys.readouterr().err == ""


def test_get_git_head_sha_uses_windows_safe_subprocess_kwargs(monkeypatch, tmp_path):
    """O subprocess do git deve redirecionar stdin, ter timeout e passar
    creationflags (CREATE_NO_WINDOW no Windows; 0 em POSIX)."""
    import subprocess

    captured_kwargs = {}

    class _FakeResult:
        stdout = "abc123\n"

    def fake_run(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr("subprocess.run", fake_run)

    assert get_git_head_sha(tmp_path) == "abc123"
    assert captured_kwargs["stdin"] == subprocess.DEVNULL
    assert captured_kwargs["timeout"] == 10
    assert "creationflags" in captured_kwargs


def test_index_workspace_full_generates_graph_json_and_graph_html(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "a.py").write_text(
        "import b\n# WHY: cache local\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    (workspace_dir / "b.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (workspace_dir / "dec-002.md").write_text("# Decisão 002\n\ntexto\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        index_workspace(workspace_dir, index_dir)

    graph = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
    html = (index_dir / "graph.html").read_text(encoding="utf-8")

    assert graph["graph_version"] == "1.0"
    assert "application/json" in html


def test_index_workspace_incremental_regenerates_graph_after_delete(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    file_a = workspace_dir / "a.py"
    file_b = workspace_dir / "b.py"
    file_a.write_text("import b\n\ndef run():\n    return helper()\n", encoding="utf-8")
    file_b.write_text("def helper():\n    return 1\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        index_workspace(workspace_dir, index_dir)
        before = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
        file_b.unlink()
        index_workspace(workspace_dir, index_dir)
        after = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))

    before_ids = {node["id"] for node in before["nodes"]}
    after_ids = {node["id"] for node in after["nodes"]}
    assert "file:b.py" in before_ids
    assert "file:b.py" not in after_ids


def test_manifest_files_imports_is_updated_and_cleaned_for_deleted_files(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    file_a = workspace_dir / "a.py"
    file_b = workspace_dir / "b.py"
    file_a.write_text("import b\n\ndef run():\n    return helper()\n", encoding="utf-8")
    file_b.write_text("def helper():\n    return 1\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        index_workspace(workspace_dir, index_dir)
        manifest_before = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
        file_b.unlink()
        index_workspace(workspace_dir, index_dir)
        manifest_after = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest_before["files_imports"]["a.py"] == ["b"]
    assert "b.py" not in manifest_after["files_imports"]


def test_graph_build_exception_does_not_fail_indexing(tmp_path, capsys):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "a.py").write_text("def run():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
        patch("codesteer_atlas.indexer.build_and_write", side_effect=RuntimeError("boom")),
    ):
        stats = index_workspace(workspace_dir, index_dir)

    assert stats.files_processed == 1
    assert "Falha ao reconstruir graph.json" in capsys.readouterr().err


def test_index_progress_reporter_emits_graph_phase(tmp_path, capsys):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "app.py").write_text("def run_app():\n    pass\n", encoding="utf-8")

    index_dir = tmp_path / "index_output"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        index_workspace(workspace_dir, index_dir, report_progress=True)

    captured = capsys.readouterr()
    assert "Reconstruindo grafo" in captured.err


def test_phase_weights_somam_um():
    """
    Os pesos de fase alimentam o cálculo de progresso; se não somarem 1.0 o reporte
    fica errado, e uma fase sem label quebra `tick`/`phase_done` com KeyError.
    """
    from codesteer_atlas.indexer import _PHASE_LABELS, _PHASE_WEIGHTS

    assert abs(sum(_PHASE_WEIGHTS.values()) - 1.0) < 1e-9
    assert set(_PHASE_WEIGHTS) == set(_PHASE_LABELS)


def test_index_workspace_gera_brief_json(tmp_path):
    """A indexação produz o briefing pré-computado junto com o grafo."""
    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    (workspace_dir / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    index_dir = tmp_path / ".code-index"

    with patch(
        "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
    ):
        stats = index_workspace(workspace_dir, index_dir)

    brief_path = index_dir / "brief.json"
    assert brief_path.exists()
    assert stats.brief_status == "full"
    assert stats.brief_bytes > 0
    assert stats.brief_layers >= 1

    payload = json.loads(brief_path.read_text(encoding="utf-8"))
    assert payload["brief_version"] == "1.0"
    assert payload["identity"]["files"] == 1


def test_indexacao_nao_falha_quando_brief_falha(tmp_path):
    """
    Uma falha ao gerar o brief é degradação, não erro fatal: a indexação em si
    (chunks no LanceDB) precisa concluir normalmente.
    """
    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    (workspace_dir / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    index_dir = tmp_path / ".code-index"

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch(
            "codesteer_atlas.indexer.build_and_write_brief",
            side_effect=RuntimeError("falha simulada"),
        ),
    ):
        stats = index_workspace(workspace_dir, index_dir)

    assert stats.brief_status == "failed"
    assert stats.chunks_persisted > 0
    assert not (index_dir / "brief.json").exists()


def test_indexacao_aborta_em_api_de_parser_incompativel(tmp_path):
    """
    Ambiente com API de parser errada tem de abortar a indexação, não produzir um
    índice vazio e reportar sucesso — foi exatamente essa a falha silenciosa que
    degradou o índice em produção.
    """
    from codesteer_atlas.chunker import ASTChunker, IncompatibleParserError

    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    (workspace_dir / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    index_dir = tmp_path / ".code-index"

    with patch.object(
        ASTChunker, "chunk_file", side_effect=IncompatibleParserError("api incompativel")
    ), pytest.raises(IncompatibleParserError):
        index_workspace(workspace_dir, index_dir)


def test_falha_de_arquivo_e_contada_e_nao_aborta(tmp_path):
    """
    Uma falha pontual de arquivo continua sendo tolerada, mas passa a ser CONTADA em
    `files_failed` para que o índice incompleto seja detectável.
    """
    from codesteer_atlas.chunker import ASTChunker

    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    (workspace_dir / "ok.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (workspace_dir / "ruim.py").write_text("def ruim():\n    return 2\n", encoding="utf-8")
    index_dir = tmp_path / ".code-index"

    original = ASTChunker.chunk_file

    def _falha_em_ruim(self, file_path, repo_name):
        if file_path.name == "ruim.py":
            raise ValueError("arquivo problematico")
        return original(self, file_path, repo_name)

    with (
        patch(
            "codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode
        ),
        patch.object(ASTChunker, "chunk_file", _falha_em_ruim),
    ):
        stats = index_workspace(workspace_dir, index_dir)

    assert stats.files_failed == 1
    assert stats.files_processed == 1
    assert stats.chunks_persisted > 0


# --- §3.3 / DECISÃO-003: índice de declarações e versão do índice --------------


def _f3_workspace(tmp_path):
    """Workspace Go + Java + C# com imports que só a §3.3 resolve."""
    workspace_dir = tmp_path / "workspace"
    (workspace_dir / "internal" / "svc").mkdir(parents=True)
    (workspace_dir / "cmd").mkdir(parents=True)
    (workspace_dir / "Web").mkdir(parents=True)
    (workspace_dir / "Servicos").mkdir(parents=True)

    (workspace_dir / "go.mod").write_text("module github.com/acme/app\n\ngo 1.22\n", encoding="utf-8")
    (workspace_dir / "cmd" / "main.go").write_text(
        'package main\n\nimport "github.com/acme/app/internal/svc"\n\nfunc main() { svc.Run() }\n',
        encoding="utf-8",
    )
    (workspace_dir / "internal" / "svc" / "svc.go").write_text(
        "package svc\n\nfunc Run() {}\n", encoding="utf-8"
    )
    (workspace_dir / "App.java").write_text(
        "package com.acme.web;\n\nimport com.acme.core.Service;\n\nclass App {}\n",
        encoding="utf-8",
    )
    (workspace_dir / "Service.java").write_text(
        "package com.acme.core;\n\npublic class Service {}\n", encoding="utf-8"
    )
    (workspace_dir / "Web" / "Home.cs").write_text(
        "namespace MinhaApp.Web;\n\nusing MinhaApp.Servicos;\n\nclass Home {}\n", encoding="utf-8"
    )
    (workspace_dir / "Servicos" / "A.cs").write_text(
        "namespace MinhaApp.Servicos;\n\npublic class A {}\n", encoding="utf-8"
    )
    return workspace_dir


def test_index_workspace_persiste_files_declares_e_arestas_multi_linguagem(tmp_path):
    """
    Go, Java e C# produziam ZERO arestas `imports` antes da §3.3. Este teste conta
    as arestas por linguagem — "não levantou erro" não provaria nada aqui.
    """
    workspace_dir = _f3_workspace(tmp_path)
    index_dir = tmp_path / "index_output"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        index_workspace(workspace_dir, index_dir)

    manifest = StorageBackend(index_dir=index_dir).get_manifest()
    assert manifest.index_version == "2.3.0"
    assert manifest.files_declares == {
        "App.java": "com.acme.web",
        "Service.java": "com.acme.core",
        "Web/Home.cs": "MinhaApp.Web",
        "Servicos/A.cs": "MinhaApp.Servicos",
    }

    graph = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
    imports = {
        (edge["source"], edge["target"]) for edge in graph["edges"] if edge["kind"] == "imports"
    }
    assert imports == {
        ("file:cmd/main.go", "file:internal/svc/svc.go"),
        ("file:App.java", "file:Service.java"),
        ("file:Web/Home.cs", "file:Servicos/A.cs"),
    }
    assert all(
        edge["origin"] == "treesitter" for edge in graph["edges"] if edge["kind"] == "imports"
    )

    coverage = graph["resolution_coverage"]
    assert set(coverage["treesitter"]) == {"go", "java", "csharp"}
    assert coverage["scip"] == []
    assert coverage["files_unresolved"] == 0


def test_files_declares_some_para_arquivo_deletado(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    keep = workspace_dir / "Keep.java"
    keep.write_text("package com.acme.a;\nclass Keep {}\n", encoding="utf-8")
    removed = workspace_dir / "Gone.java"
    removed.write_text("package com.acme.b;\nclass Gone {}\n", encoding="utf-8")
    index_dir = tmp_path / "index_output"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="git_sha_1"),
    ):
        index_workspace(workspace_dir, index_dir)
        assert StorageBackend(index_dir=index_dir).get_manifest().files_declares == {
            "Keep.java": "com.acme.a",
            "Gone.java": "com.acme.b",
        }

        removed.unlink()
        index_workspace(workspace_dir, index_dir)

    assert StorageBackend(index_dir=index_dir).get_manifest().files_declares == {
        "Keep.java": "com.acme.a"
    }


def test_manifest_antigo_sem_files_declares_carrega_pelo_default(tmp_path):
    """Manifest 2.1.0 não tem a chave; o default vazio evita erro de validação."""
    from codesteer_atlas.models import IndexManifest

    manifest = IndexManifest.model_validate(
        {
            "total_chunks": 1,
            "repos_indexed": ["demo"],
            "embedding_model": "m",
            "embedding_dim": 384,
            "last_indexed_at": "2026-06-05T12:00:00Z",
            "languages_indexed": ["python"],
            "index_version": "2.1.0",
            "files": {"a.py": "h"},
        }
    )

    assert manifest.files_declares == {}


# ---------------------------------------------------------------------------
# TASK-018 / TASK-019 — fase `scip` no pipeline de indexação
# ---------------------------------------------------------------------------


def _workspace_com_chamada(tmp_path):
    """`run_app` (app.py) chama `helper` (util.py); ambos ocupam as linhas 1-2."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "app.py").write_text(
        "def run_app():\n    return helper()\n", encoding="utf-8"
    )
    (workspace_dir / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    return workspace_dir


def _scip_fixture_bytes():
    from tests.test_scip_ingest import enc_document, enc_index, enc_occurrence

    return enc_index(
        [
            enc_document(
                "app.py",
                [
                    enc_occurrence("sym-run", 0, 1, definition=True),
                    enc_occurrence("sym-helper", 1, 1),
                ],
            ),
            enc_document("util.py", [enc_occurrence("sym-helper", 0, 1, definition=True)]),
        ]
    )


def test_fase_scip_desligada_por_padrao_nao_invoca_subprocesso(tmp_path, monkeypatch):
    """Nenhuma indexação atual pode passar a executar um binário externo."""
    monkeypatch.delenv("ATLAS_SCIP", raising=False)
    workspace_dir = _workspace_com_chamada(tmp_path)
    index_dir = tmp_path / "index_output"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
        patch("codesteer_atlas.indexer._run_scip_phase") as fase_mock,
    ):
        stats = index_workspace(workspace_dir, index_dir)

    fase_mock.assert_not_called()
    assert stats.scip_status == "disabled"
    assert stats.scip_edges == 0
    assert "scip" not in stats.phase_durations_s
    graph = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
    assert "scip" not in graph
    assert [edge for edge in graph["edges"] if edge["kind"] == "calls"] == []


def test_fase_scip_ligada_grava_arestas_calls_no_grafo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_SCIP", "1")
    workspace_dir = _workspace_com_chamada(tmp_path)
    index_dir = tmp_path / "index_output"
    payload = _scip_fixture_bytes()

    def _fake_run(binary, argv_tail, workspace_root, output_path):
        Path(output_path).write_bytes(payload)
        return "ok"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
        patch(
            "codesteer_atlas.scip_ingest.detect_toolchains",
            return_value={"python": "/usr/bin/scip-python"},
        ),
        patch("codesteer_atlas.scip_ingest.run_indexer", side_effect=_fake_run),
    ):
        stats = index_workspace(workspace_dir, index_dir, report_progress=False)

    assert stats.scip_status == "ok"
    assert stats.scip_edges == 1
    assert "scip" in stats.phase_durations_s
    graph = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
    assert [edge for edge in graph["edges"] if edge["kind"] == "calls"] == [
        {
            "source": "sym:app.py#run_app",
            "target": "sym:util.py#helper",
            "kind": "calls",
            "origin": "scip",
            "location": {"file_path": "app.py", "lines": [2, 2]},
        }
    ]
    assert graph["scip"] == {
        "status": "ok",
        "head_sha": "sha-1",
        "languages": ["python"],
        "edges": 1,
    }
    assert graph["resolution_coverage"]["scip"] == ["python"]
    assert "python" not in graph["resolution_coverage"]["treesitter"]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "status_do_run, status_esperado",
    [("timeout", "timeout"), ("parse_failed", "parse_failed")],
)
def test_fase_scip_degradada_conclui_a_indexacao(
    tmp_path, monkeypatch, capsys, status_do_run, status_esperado
):
    """Timeout e índice ilegível viram status; `files_processed`/`chunks_persisted` intactos."""
    monkeypatch.setenv("ATLAS_SCIP", "1")
    workspace_dir = _workspace_com_chamada(tmp_path)
    index_dir = tmp_path / "index_output"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
        patch(
            "codesteer_atlas.scip_ingest.detect_toolchains",
            return_value={"python": "/usr/bin/scip-python"},
        ),
        patch("codesteer_atlas.scip_ingest.run_indexer", return_value=status_do_run),
    ):
        stats = index_workspace(workspace_dir, index_dir, report_progress=False)

    assert stats.scip_status == status_esperado
    assert stats.scip_edges == 0
    assert stats.files_processed == 2
    assert stats.chunks_persisted == 2
    graph = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
    assert graph["scip"]["status"] == status_esperado
    assert graph["resolution_coverage"]["scip"] == []
    assert capsys.readouterr().out == ""


def test_fase_scip_sem_toolchain_reporta_toolchain_missing_e_conclui(tmp_path, monkeypatch):
    """Nenhum indexador instalado (caso real desta máquina): indexação termina normal."""
    monkeypatch.setenv("ATLAS_SCIP", "1")
    workspace_dir = _workspace_com_chamada(tmp_path)
    index_dir = tmp_path / "index_output"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
        patch("codesteer_atlas.scip_ingest.subprocess.run") as run_mock,
    ):
        stats = index_workspace(workspace_dir, index_dir, report_progress=False)

    run_mock.assert_not_called()
    assert stats.scip_status == "toolchain_missing"
    assert stats.files_processed == 2
    assert stats.chunks_persisted == 2


def test_fase_scip_pula_incremental_com_head_inalterado(tmp_path, monkeypatch):
    """DECISÃO-002: indexador SCIP é whole-project; sem HEAD novo, preserva o que há."""
    monkeypatch.setenv("ATLAS_SCIP", "1")
    workspace_dir = _workspace_com_chamada(tmp_path)
    (workspace_dir / "outro.py").write_text("def outro():\n    return 0\n", encoding="utf-8")
    index_dir = tmp_path / "index_output"
    payload = _scip_fixture_bytes()

    def _fake_run(binary, argv_tail, workspace_root, output_path):
        Path(output_path).write_bytes(payload)
        return "ok"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
        patch(
            "codesteer_atlas.scip_ingest.detect_toolchains",
            return_value={"python": "/usr/bin/scip-python"},
        ),
        patch("codesteer_atlas.scip_ingest.run_indexer", side_effect=_fake_run) as run_mock,
    ):
        index_workspace(workspace_dir, index_dir, report_progress=False)
        (workspace_dir / "outro.py").write_text("def outro():\n    return 1\n", encoding="utf-8")
        segunda = index_workspace(workspace_dir, index_dir, report_progress=False)

    assert segunda.graph_strategy == "incremental-code"
    assert run_mock.call_count == 1  # a 2ª execução não reinvoca o indexador
    assert segunda.scip_status == "ok"
    assert segunda.scip_edges == 1
    graph = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
    assert graph["scip"] == {
        "status": "ok",
        "head_sha": "sha-1",
        "languages": ["python"],
        "edges": 1,
    }
    assert graph["resolution_coverage"]["scip"] == ["python"]
    assert [edge["source"] for edge in graph["edges"] if edge["kind"] == "calls"] == [
        "sym:app.py#run_app"
    ]


def test_rebuild_incremental_do_arquivo_alterado_derruba_suas_arestas_calls(
    tmp_path, monkeypatch
):
    """O símbolo é re-chunkado; a aresta SCIP dele fica velha e não é preservada."""
    monkeypatch.setenv("ATLAS_SCIP", "1")
    workspace_dir = _workspace_com_chamada(tmp_path)
    index_dir = tmp_path / "index_output"
    payload = _scip_fixture_bytes()

    def _fake_run(binary, argv_tail, workspace_root, output_path):
        Path(output_path).write_bytes(payload)
        return "ok"

    with (
        patch("codesteer_atlas.embeddings.EmbeddingEngine.encode", side_effect=_patched_encode),
        patch("codesteer_atlas.indexer.get_git_head_sha", return_value="sha-1"),
        patch(
            "codesteer_atlas.scip_ingest.detect_toolchains",
            return_value={"python": "/usr/bin/scip-python"},
        ),
        patch("codesteer_atlas.scip_ingest.run_indexer", side_effect=_fake_run),
    ):
        index_workspace(workspace_dir, index_dir, report_progress=False)
        (workspace_dir / "app.py").write_text(
            "def run_app():\n    return helper() + 1\n", encoding="utf-8"
        )
        segunda = index_workspace(workspace_dir, index_dir, report_progress=False)

    graph = json.loads((index_dir / "graph.json").read_text(encoding="utf-8"))
    assert [edge for edge in graph["edges"] if edge["kind"] == "calls"] == []
    assert segunda.scip_edges == 0
    # A declaração sobrevive ao rebuild: o índice não volta a se dizer sem SCIP
    assert graph["scip"]["status"] == "ok"
    assert graph["scip"]["head_sha"] == "sha-1"
