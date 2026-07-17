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


def test_graph_html_has_no_external_urls(tmp_path):
    graph = {
        "workspace_repo": "repo",
        "generated_at": "2026-06-05T12:00:00Z",
        "nodes": [],
        "edges": [],
        "metrics": {"node_count": 0, "edge_count": 0, "top_hubs": []},
    }

    html = write_graph_html(graph, tmp_path).read_text(encoding="utf-8")

    assert re.search(r"https?://|//cdn", html) is None


def test_graph_html_escapes_script_breakout_sequences(tmp_path):
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


def test_viewer_template_has_obsidian_like_dark_theme_and_on_demand_labels(tmp_path):
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
    assert "focusLabelZoomThreshold" in html
    assert "labelNodeIds(visibility)" in html
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
    assert "Math.min(window.devicePixelRatio || 1, pixelRatioCap)" in html
    assert "const visibleCache = {" in html
    assert 'const debugPanel = document.getElementById("debug-panel");' in html
    assert "Layout rapido aplicado" in html
