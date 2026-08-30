import json
from unittest.mock import patch

import pytest

from codesteer_atlas.config import GRAPH_AFFECTED_MAX_RESULTS, GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND
from codesteer_atlas.graph import (
    _clear_graph_cache,
    affected,
    bfs_path,
    build_and_write,
    explain,
    hubs,
    is_noise_hub,
    load_graph,
    resolve_node,
)
from codesteer_atlas.models import CodeChunk
from codesteer_atlas.storage import StorageBackend

MOCK_VECTOR = [0.0] * 384


@pytest.fixture
def temp_storage(tmp_path):
    return StorageBackend(index_dir=tmp_path)


def _make_base_graph_index(temp_storage):
    chunks = [
        CodeChunk(
            id="sym-a",
            file_path="pkg/a.py",
            repo="test-project",
            start_line=1,
            end_line=8,
            scope_type="function",
            scope_name="run",
            language="python",
            content="def run():\n    pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
            references=["cite:dec-002", "why:cache evita lookup"],
        ),
        CodeChunk(
            id="sym-b",
            file_path="pkg/b.py",
            repo="test-project",
            start_line=1,
            end_line=5,
            scope_type="function",
            scope_name="helper",
            language="python",
            content="def helper():\n    pass",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="sec-overview",
            file_path="docs/overview.md",
            repo="test-project",
            start_line=1,
            end_line=4,
            scope_type="section",
            scope_name="Overview",
            language="markdown",
            content="# Overview\n\nVeja [decisão](dec-002-resolucao.md).",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="sec-dec",
            file_path="docs/dec-002-resolucao.md",
            repo="test-project",
            start_line=1,
            end_line=4,
            scope_type="section",
            scope_name="Decisão 002",
            language="markdown",
            content="# Decisão 002\n\nDetalhes.",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(chunks)
    manifest = temp_storage.get_manifest().model_copy(
        update={
            "files": {
                "pkg/a.py": "sha-a",
                "pkg/b.py": "sha-b",
                "docs/overview.md": "sha-c",
                "docs/dec-002-resolucao.md": "sha-d",
            },
            "files_imports": {"pkg/a.py": ["pkg.b", "os"]},
        }
    )
    return manifest


def test_build_generates_nodes_and_edges_for_all_core_kinds(temp_storage):
    manifest = _make_base_graph_index(temp_storage)

    graph_path = build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    kinds = {node["kind"] for node in graph["nodes"]}
    edge_kinds = {edge["kind"] for edge in graph["edges"]}
    assert {"file", "doc", "symbol", "section", "rationale"} <= kinds
    assert {"contains", "links_to", "cites", "annotates", "imports"} <= edge_kinds


def test_unresolved_cite_does_not_create_ghost_node(temp_storage):
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="c1",
                file_path="pkg/a.py",
                repo="test-project",
                start_line=1,
                end_line=3,
                scope_type="function",
                scope_name="run",
                language="python",
                content="def run():\n    pass",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
                references=["cite:dec-999"],
            )
        ]
    )
    manifest = temp_storage.get_manifest().model_copy(
        update={"files": {"pkg/a.py": "sha-a"}, "files_imports": {}}
    )

    graph = json.loads(build_and_write(temp_storage, manifest, temp_storage.index_dir).read_text())

    assert all(node["id"] != "file:dec-999" for node in graph["nodes"])
    assert not any(edge["kind"] == "cites" for edge in graph["edges"])


def test_workspace_without_markdown_still_produces_valid_graph_and_queries(temp_storage):
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="a1",
                file_path="pkg/a.py",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="run",
                language="python",
                content="def run():\n    pass",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
                references=["why:usa cache"],
            ),
            CodeChunk(
                id="b1",
                file_path="pkg/b.py",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="helper",
                language="python",
                content="def helper():\n    pass",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
        ]
    )
    manifest = temp_storage.get_manifest().model_copy(
        update={
            "files": {"pkg/a.py": "sha-a", "pkg/b.py": "sha-b"},
            "files_imports": {"pkg/a.py": ["pkg.b"]},
        }
    )

    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    assert graph["metrics"]["node_count"] >= 5
    assert bfs_path(graph, "pkg/a.py", "pkg/b.py")["found"] is True
    assert explain(graph, "pkg/a.py")["notes"] == []


def test_hubs_degree_excludes_contains_and_is_sorted_desc(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    result = hubs(graph, 3)

    assert result == sorted(result, key=lambda item: (-item["degree"], item["id"]))
    assert result[0]["degree"] >= result[-1]["degree"]


def test_bfs_path_reports_edge_kinds_and_missing_path(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    found = bfs_path(graph, "pkg/a.py", "docs/dec-002-resolucao.md")
    missing = bfs_path(graph, "pkg/b.py", "docs/overview.md", max_hops=1)

    assert found["found"] is True
    assert any(step["edge_kind_to_next"] == "cites" for step in found["path"][:-1])
    assert missing["found"] is False


def test_resolve_node_prefers_id_then_label_then_unique_suffix_and_reports_ambiguity(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    assert resolve_node(graph, "file:pkg/a.py")["id"] == "file:pkg/a.py"
    assert resolve_node(graph, "Overview")["id"] == "sec:docs/overview.md#Overview"
    assert resolve_node(graph, "pkg/b.py")["id"] == "file:pkg/b.py"

    graph["_nodes_by_id"]["sym:pkg/other.py#helper"] = {
        "id": "sym:pkg/other.py#helper",
        "kind": "symbol",
        "label": "helper",
        "file_path": "pkg/other.py",
        "lines": [1, 2],
    }
    try:
        resolve_node(graph, "helper")
    except ValueError as e:
        assert "candidatos" in str(e).lower() or "candidates" in str(e).lower()
    else:
        raise AssertionError("Esperava ValueError para referência ambígua")


def test_graph_write_is_atomic_and_json_is_valid(temp_storage):
    manifest = _make_base_graph_index(temp_storage)

    graph_path = build_and_write(temp_storage, manifest, temp_storage.index_dir)

    assert graph_path.exists()
    assert not (temp_storage.index_dir / "graph.json.tmp").exists()
    assert json.loads(graph_path.read_text(encoding="utf-8"))["graph_version"] == "1.0"


def test_load_graph_reuses_process_cache(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)

    first = load_graph(temp_storage.index_dir)

    with patch("builtins.open", side_effect=AssertionError("nao deveria reler graph.json")):
        second = load_graph(temp_storage.index_dir)

    assert first is second


def test_python_import_resolution_ignores_stdlib(temp_storage):
    manifest = _make_base_graph_index(temp_storage)
    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)

    import_edges = [edge for edge in graph["edges"] if edge["kind"] == "imports"]

    assert import_edges == [{"source": "file:pkg/a.py", "target": "file:pkg/b.py", "kind": "imports"}]


def test_relative_ts_imports_resolve_with_suffixes_and_bare_imports_are_ignored(temp_storage):
    temp_storage.store_chunks(
        [
            CodeChunk(
                id="ts-app",
                file_path="src/app.ts",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="app",
                language="typescript",
                content="export const app = 1",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
            CodeChunk(
                id="ts-lib",
                file_path="src/lib.tsx",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="lib",
                language="typescript",
                content="export const lib = 1",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
            CodeChunk(
                id="ts-index",
                file_path="src/dir/index.ts",
                repo="test-project",
                start_line=1,
                end_line=5,
                scope_type="function",
                scope_name="dir",
                language="typescript",
                content="export const dir = 1",
                indexed_at="2026-06-05T12:00:00Z",
                vector=MOCK_VECTOR,
            ),
        ]
    )
    manifest = temp_storage.get_manifest().model_copy(
        update={
            "files": {
                "src/app.ts": "sha-a",
                "src/lib.tsx": "sha-b",
                "src/dir/index.ts": "sha-c",
            },
            "files_imports": {"src/app.ts": ["./lib", "./dir", "react"]},
        }
    )

    build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = load_graph(temp_storage.index_dir)
    import_targets = sorted(
        edge["target"] for edge in graph["edges"] if edge["kind"] == "imports"
    )

    assert import_targets == ["file:src/dir/index.ts", "file:src/lib.tsx"]


# --- Resolução de imports absolutos em layout src/ --------------------------


def test_infer_package_roots_detecta_src_layout():
    """A raiz de código é o pai do pacote mais externo, deduzida pelos __init__.py."""
    from codesteer_atlas.graph import infer_package_roots

    files = {
        "src/demo/__init__.py",
        "src/demo/core.py",
        "src/demo/sub/__init__.py",
        "src/demo/sub/util.py",
        "tests/test_core.py",
    }

    roots = infer_package_roots(files)

    assert "" in roots
    assert "src" in roots
    # Determinístico e da raiz mais rasa para a mais profunda
    assert roots == sorted(roots, key=lambda root: (len(root), root))


def test_infer_package_roots_em_layout_plano():
    """Sem `src/`, a raiz continua sendo a do workspace — nada de raiz inventada."""
    from codesteer_atlas.graph import infer_package_roots

    roots = infer_package_roots({"demo/__init__.py", "demo/core.py"})

    assert roots == [""]


def test_resolve_module_path_absoluto_em_src_layout():
    """
    `import demo.core` precisa casar com `src/demo/core.py`: as chaves do manifest são
    relativas ao workspace, então sem raiz de pacote nenhum import absoluto resolvia.
    """
    from codesteer_atlas.graph import resolve_module_path

    files = {"src/demo/__init__.py", "src/demo/core.py", "src/demo/sub/__init__.py"}

    assert resolve_module_path("demo.core", files) == "src/demo/core.py"
    assert resolve_module_path("demo", files) == "src/demo/__init__.py"
    assert resolve_module_path("demo.sub", files) == "src/demo/sub/__init__.py"
    assert resolve_module_path("inexistente.modulo", files) is None


def test_resolve_python_import_relativo_continua_funcionando():
    """Imports relativos se ancoram no próprio arquivo e não devem usar raízes."""
    from codesteer_atlas.graph import _resolve_python_import

    files = {"src/demo/core.py", "src/demo/util.py"}

    resolvido = _resolve_python_import("src/demo/core.py", ".util", files)

    assert resolvido == "src/demo/util.py"


def _make_src_layout_index(temp_storage):
    """Índice mínimo em layout `src/` com um import absoluto entre dois módulos."""
    chunks = [
        CodeChunk(
            id="sym-core",
            file_path="src/demo/core.py",
            repo="demo",
            start_line=1,
            end_line=4,
            scope_type="function",
            scope_name="run",
            language="python",
            content="def run():\n    return helper()",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
        CodeChunk(
            id="sym-util",
            file_path="src/demo/util.py",
            repo="demo",
            start_line=1,
            end_line=3,
            scope_type="function",
            scope_name="helper",
            language="python",
            content="def helper():\n    return 1",
            indexed_at="2026-06-05T12:00:00Z",
            vector=MOCK_VECTOR,
        ),
    ]
    temp_storage.store_chunks(chunks)
    return temp_storage.get_manifest().model_copy(
        update={
            "files": {
                "src/demo/__init__.py": "h0",
                "src/demo/core.py": "h1",
                "src/demo/util.py": "h2",
            },
            "files_imports": {"src/demo/core.py": ["demo.util"]},
        }
    )


def test_build_and_write_cria_aresta_imports_em_src_layout(temp_storage):
    """
    Regressão do bug que deixava o grafo praticamente sem arestas `imports`: um
    import absoluto num layout `src/` tem de virar aresta entre os dois arquivos.
    """
    manifest = _make_src_layout_index(temp_storage)
    graph_path = build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    import_edges = [edge for edge in graph["edges"] if edge["kind"] == "imports"]

    assert import_edges == [
        {
            "source": "file:src/demo/core.py",
            "target": "file:src/demo/util.py",
            "kind": "imports",
        }
    ]


def test_import_resolvido_gera_grau_para_ranqueamento(temp_storage):
    """
    A aresta `imports` tem de contar no `degree` (que ignora apenas `contains`), pois
    é dele que dependem os hubs do `atlas_graph` e o ranking do `atlas_brief`.
    """
    manifest = _make_src_layout_index(temp_storage)
    graph_path = build_and_write(temp_storage, manifest, temp_storage.index_dir)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    degrees = {node["id"]: node["degree"] for node in graph["nodes"]}

    assert degrees["file:src/demo/core.py"] == 1
    assert degrees["file:src/demo/util.py"] == 1


def _write_query_graph(index_dir, nodes, edges, top_hubs=None):
    _clear_graph_cache()
    payload = {
        "nodes": nodes,
        "edges": edges,
        "metrics": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "top_hubs": top_hubs if top_hubs is not None else [],
        },
    }
    (index_dir / "graph.json").write_text(json.dumps(payload), encoding="utf-8")
    return load_graph(index_dir)


def _file_node(path, degree=0, label=None):
    return {
        "id": f"file:{path}",
        "kind": "file",
        "label": label or path.rsplit("/", 1)[-1],
        "file_path": path,
        "lines": None,
        "degree": degree,
    }


def _sym_node(path, name, degree=0, lines=None):
    return {
        "id": f"sym:{path}#{name}",
        "kind": "symbol",
        "label": name,
        "file_path": path,
        "lines": lines or [1, 10],
        "degree": degree,
    }


def test_explain_caps_neighbors_per_kind_and_reports_truncated(tmp_path):
    hub = _file_node("pkg/hub.py", degree=20)
    neighbors = [_sym_node("pkg/hub.py", f"fn{i}", degree=20 - i) for i in range(15)]
    edges = [{"source": hub["id"], "target": n["id"], "kind": "contains"} for n in neighbors]
    graph = _write_query_graph(tmp_path, [hub, *neighbors], edges)

    result = explain(graph, hub["id"])

    assert len(result["neighbors"]["symbol"]) == GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND
    assert result["truncated"]["symbol"] == 15 - GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND
    degrees = [item["degree"] for item in result["neighbors"]["symbol"]]
    assert degrees == sorted(degrees, reverse=True)
    assert {"node", "neighbors", "rationale", "notes"} <= set(result)


def test_hubs_excludes_noise_labels_and_section_rationale(tmp_path):
    nodes = [
        {"id": "sec:doc.md#T", "kind": "section", "label": "T", "file_path": "doc.md", "degree": 99},
        {"id": "rat:abc", "kind": "rationale", "label": "why", "file_path": "a.py", "degree": 80},
        _sym_node("lib/codec.py", "json", degree=70),
        _sym_node("lib/pathlib.py", "Path", degree=60),
        _file_node("core.py", degree=5),
    ]
    top_hubs = [{"id": n["id"], "degree": n["degree"]} for n in nodes]
    graph = _write_query_graph(tmp_path, nodes, [], top_hubs=top_hubs)

    result = hubs(graph, 10)
    labels = {item["label"] for item in result}
    kinds = {item["kind"] for item in result}

    assert "json" not in labels
    assert "Path" not in labels
    assert "section" not in kinds
    assert "rationale" not in kinds
    assert "core.py" in labels
    assert is_noise_hub(nodes[2]) is True


def test_affected_follows_reverse_imports_not_undirected_adjacency(tmp_path):
    a = _file_node("pkg/a.py", degree=1)
    b = _file_node("pkg/b.py", degree=1)
    graph = _write_query_graph(
        tmp_path, [a, b], [{"source": a["id"], "target": b["id"], "kind": "imports"}]
    )

    from_b = affected(graph, b["id"])
    from_a = affected(graph, a["id"])

    assert any(item["id"] == a["id"] for item in from_b["items"])
    assert all(item["id"] != b["id"] for item in from_a["items"])


def test_affected_seeds_class_members_via_contains_without_reporting_them(tmp_path):
    cls = _sym_node("pkg/mod.py", "Service", degree=2)
    method = _sym_node("pkg/mod.py", "Service.run", degree=1)
    caller = _file_node("pkg/client.py", degree=1)
    graph = _write_query_graph(
        tmp_path,
        [cls, method, caller],
        [
            {"source": cls["id"], "target": method["id"], "kind": "contains"},
            {"source": caller["id"], "target": method["id"], "kind": "calls"},
        ],
    )

    result = affected(graph, cls["id"])
    ids = [item["id"] for item in result["items"]]

    assert caller["id"] in ids
    assert method["id"] not in ids
    assert "calls_unavailable" not in result["warnings"]


def test_affected_skips_noise_hubs_in_expansion(tmp_path):
    target = _file_node("pkg/core.py", degree=1)
    noise = _sym_node("lib/json.py", "json", degree=9)
    beyond = _file_node("pkg/beyond.py", degree=1)
    graph = _write_query_graph(
        tmp_path,
        [target, noise, beyond],
        [
            {"source": noise["id"], "target": target["id"], "kind": "imports"},
            {"source": beyond["id"], "target": noise["id"], "kind": "imports"},
        ],
    )

    result = affected(graph, target["id"])
    ids = [item["id"] for item in result["items"]]

    assert noise["id"] not in ids
    assert beyond["id"] not in ids


def test_affected_caps_at_GRAPH_AFFECTED_MAX_RESULTS(tmp_path):
    target = _file_node("pkg/lib.py", degree=GRAPH_AFFECTED_MAX_RESULTS + 5)
    dependents = [
        _file_node(f"pkg/dep{i}.py", degree=GRAPH_AFFECTED_MAX_RESULTS + 5 - i)
        for i in range(GRAPH_AFFECTED_MAX_RESULTS + 5)
    ]
    edges = [
        {"source": dep["id"], "target": target["id"], "kind": "imports"} for dep in dependents
    ]
    graph = _write_query_graph(tmp_path, [target, *dependents], edges)

    result = affected(graph, target["id"])

    assert len(result["items"]) == GRAPH_AFFECTED_MAX_RESULTS
    assert result["truncated"] == {"omitted": 5}


def test_affected_warns_calls_unavailable_when_no_calls_edges(tmp_path):
    a = _file_node("pkg/a.py", degree=1)
    b = _file_node("pkg/b.py", degree=1)
    graph = _write_query_graph(
        tmp_path, [a, b], [{"source": a["id"], "target": b["id"], "kind": "imports"}]
    )

    result = affected(graph, b["id"])

    assert "calls_unavailable" in result["warnings"]
    assert any(item["id"] == a["id"] and item["via"] == "imports" for item in result["items"])


def test_load_graph_caches_reverse_adjacency_with_same_mtime_key(tmp_path):
    a = _file_node("pkg/a.py", degree=1)
    b = _file_node("pkg/b.py", degree=1)
    first = _write_query_graph(
        tmp_path, [a, b], [{"source": a["id"], "target": b["id"], "kind": "imports"}]
    )

    with patch("builtins.open", side_effect=AssertionError("nao deveria reler graph.json")):
        second = load_graph(tmp_path)

    assert first is second
    assert first["_reverse_adjacency"] is second["_reverse_adjacency"]
    assert a["id"] in first["_reverse_adjacency"][b["id"]][0]

