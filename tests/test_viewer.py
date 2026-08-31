import json
import re

from codesteer_atlas.config import GRAPH_VIEWER_MAX_FULL_NODES
from codesteer_atlas.viewer import write_graph_html


def _extract_json_payload(html: str) -> dict:
    match = re.search(
        r'<script id="graph-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_write_graph_html_embeds_parseable_graph_json(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [{"id": "file:src/app.py", "kind": "file", "label": "app.py", "degree": 1}],
        "edges": [],
        "metrics": {"node_count": 1, "edge_count": 0, "top_hubs": []},
    }

    output = write_graph_html(graph, tmp_path)
    html = output.read_text(encoding="utf-8")

    embedded = _extract_json_payload(html)
    assert embedded["workspace_repo"] == "repo"
    assert embedded["viewer"]["hubs_only"] is False
    assert embedded["viewer"]["node_count"] == 1
    assert embedded["viewer"]["edge_count"] == 0
    assert embedded["viewer"]["layout_mode"] == "light-relaxed"
    assert embedded["viewer"]["render_profile"]["label_mode"] == "focus-only"
    assert embedded["viewer"]["render_profile"]["max_zoom"] == 10


def test_graph_html_has_no_external_resource_loads(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [],
        "edges": [],
        "metrics": {"node_count": 0, "edge_count": 0, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")

    # O force-graph e vendorizado inline: nao pode haver nenhum carregamento
    # externo (CDN, <script src>, <link>, @import ou url(http)) — abre via file://.
    # URIs de namespace (http://www.w3.org/...) dentro do bundle nao sao rede.
    assert "<script src=" not in html
    assert "<link " not in html
    assert "@import" not in html
    assert "//cdn" not in html
    assert re.search(r'src\s*=\s*["\']https?://', html) is None
    assert re.search(r'href\s*=\s*["\']https?://', html) is None
    assert re.search(r"url\(\s*['\"]?https?://", html) is None
    assert re.search(r"\b(fetch|XMLHttpRequest|WebSocket)\s*\(", html) is None
    # ... e a lib force-graph esta de fato embutida.
    assert "ForceGraph" in html


def test_graph_html_embeds_vendored_force_graph_bundle(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [{"id": "n1", "kind": "symbol", "label": "Node 1", "degree": 1}],
        "edges": [],
        "metrics": {"node_count": 1, "edge_count": 0, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")

    # O placeholder foi substituido pelo bundle UMD real (nao sobra marcador).
    assert "__FORCE_GRAPH_LIB__" not in html
    # Assinatura do UMD do force-graph (exporta o global ForceGraph).
    assert ".ForceGraph=" in html
    assert len(html) > 150_000  # bundle (~177 KB) esta de fato embutido
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [
            {"id": "n1", "kind": "symbol", "label": '</script>"quoted"', "degree": 1}
        ],
        "edges": [],
        "metrics": {"node_count": 1, "edge_count": 0, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")

    assert "</script>" not in html.split('id="graph-data"', 1)[1].split("</script>", 1)[0]
    embedded = _extract_json_payload(html)
    assert embedded["nodes"][0]["label"] == '</script>"quoted"'


def test_graph_html_write_is_atomic(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [],
        "edges": [],
        "metrics": {"node_count": 0, "edge_count": 0, "top_hubs": []},
    }

    output = write_graph_html(graph, tmp_path)

    assert output.exists()
    assert not (tmp_path / "graph.html.tmp").exists()


def test_large_graph_marks_hubs_only_flag_in_embed(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [
            {"id": f"n{i}", "kind": "symbol", "label": f"Node {i}", "degree": i % 7}
            for i in range(GRAPH_VIEWER_MAX_FULL_NODES + 1)
        ],
        "edges": [],
        "metrics": {
            "node_count": GRAPH_VIEWER_MAX_FULL_NODES + 1,
            "edge_count": 0,
            "top_hubs": [{"id": "n0", "degree": 7}],
        },
    }

    embedded = _extract_json_payload(
        write_graph_html(graph, tmp_path).read_text(encoding="utf-8")
    )

    assert embedded["viewer"]["hubs_only"] is True
    assert embedded["viewer"]["focus_node_ids"] == ["n0"]
    assert embedded["viewer"]["highlight_hub_ids"] == ["n0"]
    assert embedded["viewer"]["notice"] == "Grafo grande: exibindo hubs e vizinhanca 1-hop por padrao."


def test_write_graph_html_embeds_noise_hub_ids(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [
            {"id": "sym:lib/json.py#json", "kind": "symbol", "label": "json", "degree": 40},
            {"id": "file:src/app.py", "kind": "file", "label": "app.py", "degree": 3},
        ],
        "edges": [],
        "metrics": {
            "node_count": 2,
            "edge_count": 0,
            "top_hubs": [{"id": "file:src/app.py", "degree": 3}],
        },
    }

    embedded = _extract_json_payload(
        write_graph_html(graph, tmp_path).read_text(encoding="utf-8")
    )

    assert "sym:lib/json.py#json" in embedded["viewer"]["noise_hub_ids"]
    assert "file:src/app.py" not in embedded["viewer"]["noise_hub_ids"]


def test_noise_nodes_do_not_break_offline_file_url_invariant(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [
            {"id": "sym:lib/json.py#json", "kind": "symbol", "label": "json", "degree": 40},
        ],
        "edges": [],
        "metrics": {"node_count": 1, "edge_count": 0, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")

    assert "<script src=" not in html
    assert "//cdn" not in html
    assert "noiseHubIds" in html


def test_viewer_template_embeds_force_graph_with_dark_theme_and_on_demand_labels(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [{"id": "n1", "kind": "symbol", "label": "Node 1", "degree": 1}],
        "edges": [],
        "metrics": {"node_count": 1, "edge_count": 0, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")

    assert "color-scheme: dark" in html
    assert 'class="shell"' in html
    # Renderer force-graph vendorizado e inicializado inline sobre <div id="graph">.
    assert "ForceGraph()" in html
    assert 'ForceGraph()(elements.graph)' in html
    # Labels sob demanda continuam guiadas pelos thresholds de zoom.
    assert "focusLabelZoomThreshold" in html
    assert "drawNodeDecorations" in html
    assert 'viewer.render_profile?.label_zoom_threshold || 0.82' in html


def test_viewer_template_has_performance_guardrails_and_debug_hooks(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [{"id": "n1", "kind": "symbol", "label": "Node 1", "degree": 1}],
        "edges": [],
        "metrics": {"node_count": 1, "edge_count": 0, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")

    assert 'viewer.render_profile?.physics_threshold || 250' in html
    assert 'viewer.render_profile?.max_zoom || 10' in html
    # Fisica limitada em grafos grandes (cooldown menor) + painel de debug via ?debug=1.
    assert "cooldownTicks" in html
    assert 'id="debug-panel"' in html
    assert "debugEnabled" in html


def test_write_graph_html_embeds_edge_origin_and_dashes_by_tier(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [
            {"id": "file:src/a.py", "kind": "file", "label": "a.py", "degree": 1},
            {"id": "file:src/b.py", "kind": "file", "label": "b.py", "degree": 1},
            {"id": "sym:src/a.py#f", "kind": "symbol", "label": "f", "degree": 1},
        ],
        "edges": [
            {
                "source": "file:src/a.py",
                "target": "file:src/b.py",
                "kind": "imports",
                "origin": "treesitter",
            },
            {
                "source": "sym:src/a.py#f",
                "target": "file:src/b.py",
                "kind": "calls",
                "origin": "scip",
            },
            {"source": "file:src/a.py", "target": "sym:src/a.py#f", "kind": "contains"},
        ],
        "resolution_coverage": {
            "scip": [],
            "treesitter": ["python"],
            "none": [],
            "files_unresolved": 0,
        },
        "metrics": {"node_count": 3, "edge_count": 3, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")
    embedded = _extract_json_payload(html)

    origins = {edge["kind"]: edge.get("origin") for edge in embedded["edges"]}
    assert origins["imports"] == "treesitter"
    assert origins["calls"] == "scip"
    assert origins["contains"] is None
    # O link montado em buildVisibleData carrega a origem, e o traco a consome.
    assert "origin: edge.origin || \"unknown\"" in html
    assert "linkLineDash" in html
    assert "dashForLink" in html
    assert 'css: "dashed"' in html
    assert 'css: "dotted"' in html


def test_graph_html_legend_lists_languages_without_resolver(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [{"id": "file:main.hs", "kind": "file", "label": "main.hs", "degree": 0}],
        "edges": [],
        "resolution_coverage": {
            "scip": [],
            "treesitter": ["python", "go"],
            "none": ["haskell", "lua"],
            "files_unresolved": 7,
        },
        "metrics": {"node_count": 1, "edge_count": 0, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")
    embedded = _extract_json_payload(html)

    # Os dados do tier chegam ao HTML e a legenda os renderiza (container + funcao).
    assert embedded["resolution_coverage"]["none"] == ["haskell", "lua"]
    assert embedded["resolution_coverage"]["files_unresolved"] == 7
    assert 'id="coverage"' in html
    assert "renderCoverageLegend" in html
    assert "graph.resolution_coverage || {}" in html
    assert "arquivos sem resolver" in html


def test_graph_sem_origin_nem_coverage_continua_renderizando(tmp_path):
    # Grafo no formato 2.1.0: sem `origin` nas arestas e sem `resolution_coverage`.
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [
            {"id": "file:src/a.py", "kind": "file", "label": "a.py", "degree": 1},
            {"id": "file:src/b.py", "kind": "file", "label": "b.py", "degree": 1},
        ],
        "edges": [
            {"source": "file:src/a.py", "target": "file:src/b.py", "kind": "imports"},
        ],
        "metrics": {"node_count": 2, "edge_count": 1, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")
    embedded = _extract_json_payload(html)

    assert "origin" not in embedded["edges"][0]
    assert "resolution_coverage" not in embedded
    # Invariante offline preservada mesmo sem os campos novos.
    assert "<script src=" not in html
    assert "//cdn" not in html
