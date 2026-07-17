import json
import os
from pathlib import Path

from codesteer_atlas.config import GRAPH_HTML_FILENAME, GRAPH_VIEWER_MAX_FULL_NODES

# Bundle UMD do force-graph (d3-force embutido) vendorizado localmente para
# manter o graph.html 100% autocontido/offline (abre via file://, sem CDN).
_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
_FORCE_GRAPH_LIB_PATH = _VENDOR_DIR / "force-graph.min.js"
_FORCE_GRAPH_LIB_CACHE: str | None = None


def _load_force_graph_lib() -> str:
    """Lê (e memoiza) o bundle vendorizado do force-graph."""
    global _FORCE_GRAPH_LIB_CACHE
    if _FORCE_GRAPH_LIB_CACHE is None:
        _FORCE_GRAPH_LIB_CACHE = _FORCE_GRAPH_LIB_PATH.read_text(encoding="utf-8")
    return _FORCE_GRAPH_LIB_CACHE


_HTML_TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CodeSteer Atlas Graph</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #10141a;
      --bg-elevated: #161b22;
      --bg-strong: #0c1117;
      --panel: rgba(22, 27, 34, 0.92);
      --panel-soft: rgba(18, 23, 30, 0.82);
      --border: rgba(148, 163, 184, 0.14);
      --text: #e6edf3;
      --muted: #8b98a9;
      --accent: #7aa2f7;
      --shadow: 0 24px 60px rgba(3, 7, 18, 0.38);
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; }
    body {
      font: 13px/1.45 "Avenir Next", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(122, 162, 247, 0.10), transparent 34%),
        radial-gradient(circle at bottom right, rgba(118, 199, 183, 0.08), transparent 28%),
        linear-gradient(180deg, #11161d 0%, #0c1016 100%);
    }
    .shell {
      display: grid;
      grid-template-columns: minmax(280px, 320px) minmax(0, 1fr);
      min-height: 100vh;
    }
    .sidebar {
      position: relative;
      z-index: 1;
      padding: 18px;
      border-right: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(19, 24, 31, 0.98), rgba(13, 17, 23, 0.96));
      box-shadow: inset -1px 0 0 rgba(255, 255, 255, 0.02);
      overflow: auto;
    }
    .stage {
      position: relative;
      min-width: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 20% 15%, rgba(122, 162, 247, 0.06), transparent 24%),
        radial-gradient(circle at 80% 75%, rgba(255, 159, 110, 0.05), transparent 26%),
        linear-gradient(180deg, rgba(13, 17, 23, 0.94), rgba(10, 14, 20, 0.98));
    }
    .stage::before {
      content: "";
      position: absolute;
      inset: 0;
      background-image:
        linear-gradient(rgba(148, 163, 184, 0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(148, 163, 184, 0.03) 1px, transparent 1px);
      background-size: 36px 36px;
      opacity: 0.24;
      pointer-events: none;
    }
    .panel-block {
      margin-bottom: 14px;
      padding: 14px;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: var(--panel-soft);
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }
    .brand { padding-bottom: 16px; }
    .eyebrow {
      margin-bottom: 8px;
      color: var(--accent);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      letter-spacing: 0.01em;
    }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .section-title {
      margin-bottom: 10px;
      color: rgba(230, 237, 243, 0.92);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    .button-row {
      display: flex;
      gap: 8px;
      margin-top: 10px;
      flex-wrap: wrap;
    }
    input[type="search"] {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid rgba(148, 163, 184, 0.18);
      border-radius: 12px;
      outline: none;
      color: var(--text);
      background: rgba(9, 13, 18, 0.78);
      transition: border-color 120ms ease, background 120ms ease;
    }
    input[type="search"]:focus {
      border-color: rgba(122, 162, 247, 0.55);
      background: rgba(10, 15, 21, 0.96);
    }
    button {
      padding: 8px 11px;
      border: 1px solid rgba(148, 163, 184, 0.16);
      border-radius: 11px;
      color: var(--text);
      background: rgba(17, 23, 31, 0.92);
      cursor: pointer;
      transition: transform 120ms ease, border-color 120ms ease, background 120ms ease;
    }
    button:hover {
      transform: translateY(-1px);
      border-color: rgba(122, 162, 247, 0.40);
      background: rgba(20, 28, 37, 0.98);
    }
    .notice {
      margin-top: 12px;
      padding: 10px 11px;
      border: 1px solid rgba(122, 162, 247, 0.18);
      border-radius: 12px;
      color: #d7e3fb;
      background: rgba(34, 43, 56, 0.84);
    }
    .filter-list {
      display: grid;
      gap: 8px;
    }
    .filter-item {
      display: flex;
      align-items: center;
      gap: 8px;
      color: rgba(230, 237, 243, 0.9);
    }
    .filter-item input {
      accent-color: var(--accent);
    }
    .legend-list,
    .metric-stack,
    .details-list {
      display: grid;
      gap: 7px;
    }
    .legend-item,
    .metric-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .legend-item {
      justify-content: flex-start;
      color: rgba(230, 237, 243, 0.84);
    }
    .legend-swatch {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
      box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.08);
    }
    .details {
      min-height: 120px;
    }
    .details code {
      color: #bfdbfe;
      word-break: break-word;
    }
    .details ul {
      margin: 8px 0 0;
      padding-left: 18px;
    }
    .details li { margin: 4px 0; }
    .node-link {
      color: #bfd5ff;
      cursor: pointer;
      text-decoration: none;
      border-bottom: 1px dotted rgba(191, 213, 255, 0.44);
    }
    .node-link:hover {
      color: #ffffff;
      border-bottom-color: rgba(255, 255, 255, 0.74);
    }
    #graph {
      position: relative;
      z-index: 1;
      width: 100%;
      height: 100vh;
      cursor: grab;
    }
    #graph.dragging { cursor: grabbing; }
    #graph canvas { display: block; }
    @media (max-width: 980px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar {
        max-height: 44vh;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }
      .stage,
      #graph { min-height: 56vh; height: 56vh; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="panel-block brand">
        <div class="eyebrow">CodeSteer Atlas</div>
        <h1 id="title"></h1>
        <div class="muted" id="subtitle"></div>
      </div>

      <div class="panel-block">
        <input id="search" type="search" placeholder="Buscar por label ou id">
        <div class="button-row">
          <button id="reset-view">Recentralizar</button>
          <button id="clear-focus">Limpar foco</button>
          <button id="load-all" hidden>Expandir tudo</button>
        </div>
        <div class="notice small" id="notice" hidden></div>
        <div class="small muted" id="search-results"></div>
      </div>

      <div class="panel-block">
        <div class="section-title">Filtros</div>
        <div id="node-filters" class="filter-list"></div>
        <div id="edge-filters" class="filter-list"></div>
      </div>

      <div class="panel-block">
        <div class="section-title">Mapa</div>
        <div id="counts" class="metric-stack small"></div>
        <div id="legend" class="legend-list small"></div>
      </div>

      <div class="panel-block">
        <div class="section-title">Detalhes</div>
        <div id="details" class="details small muted">Selecione um no para focar o subgrafo local.</div>
      </div>

      <div class="panel-block" id="debug-panel" hidden>
        <div class="section-title">Debug</div>
        <div id="debug-stats" class="metric-stack small"></div>
      </div>
    </aside>

    <main class="stage">
      <div id="graph"></div>
    </main>
  </div>

  <script id="graph-data" type="application/json">__GRAPH_JSON__</script>
  <script>__FORCE_GRAPH_LIB__</script>
  <script>
    const bootstrapStartedAt = performance.now();
    const graph = JSON.parse(document.getElementById("graph-data").textContent);
    const viewer = graph.viewer || {};
    const debugEnabled =
      /(?:\\?|&)debug=1\\b/.test(location.search) ||
      /(?:^|[#&])debug(?:=1)?\\b/.test(location.hash);

    // Paleta compartilhada com o modelo de dados do grafo (graph.py).
    const colorByKind = {
      file: "#7aa2f7",
      doc: "#f1c27d",
      symbol: "#76c7b7",
      section: "#ff9f6e",
      rationale: "#b392f0",
    };
    const edgeStyleByKind = {
      contains: { color: "#5b6777", alpha: 0.14, width: 0.6 },
      imports: { color: "#7aa2f7", alpha: 0.32, width: 1.05 },
      links_to: { color: "#f1c27d", alpha: 0.36, width: 1.12 },
      cites: { color: "#76c7b7", alpha: 0.40, width: 1.20 },
      annotates: { color: "#b392f0", alpha: 0.36, width: 1.08 },
    };
    const NODE_REL_SIZE = 4;

    // Perfil de renderizacao (mesmos knobs expostos por write_graph_html).
    const physicsThreshold = viewer.render_profile?.physics_threshold || 250;
    const focusLabelZoomThreshold = viewer.render_profile?.focus_label_zoom_threshold || 0.34;
    const contextLabelZoomThreshold = viewer.render_profile?.label_zoom_threshold || 0.82;
    const minZoom = viewer.render_profile?.min_zoom || 0.18;
    const maxZoom = viewer.render_profile?.max_zoom || 10;

    const rawNodes = graph.nodes || [];
    const rawEdges = graph.edges || [];
    const nodesById = new Map(rawNodes.map(node => [node.id, node]));
    const nodeKinds = [...new Set(rawNodes.map(node => node.kind))];
    const edgeKinds = [...new Set(rawEdges.map(edge => edge.kind))];
    const activeNodeKinds = new Set(nodeKinds);
    const activeEdgeKinds = new Set(edgeKinds);
    const summaryFocusIds = new Set(viewer.focus_node_ids || []);
    const highlightHubIds = new Set(viewer.highlight_hub_ids || []);
    const nodeIdsByKind = new Map(nodeKinds.map(kind => [kind, []]));
    const adjacency = new Map(rawNodes.map(node => [node.id, []]));
    for (const node of rawNodes) {
      nodeIdsByKind.get(node.kind).push(node.id);
    }
    for (const edge of rawEdges) {
      if (adjacency.has(edge.source)) adjacency.get(edge.source).push({ id: edge.target, kind: edge.kind });
      if (adjacency.has(edge.target)) adjacency.get(edge.target).push({ id: edge.source, kind: edge.kind });
    }

    const state = {
      selectedId: null,
      hoveredId: null,
      search: "",
      showAll: !viewer.hubs_only,
      focusIds: new Set(),
      matchedIds: new Set(),
      visibleNodeCount: 0,
      visibleEdgeCount: 0,
    };
    const perf = { bootstrapMs: 0 };

    const elements = {
      graph: document.getElementById("graph"),
      title: document.getElementById("title"),
      subtitle: document.getElementById("subtitle"),
      notice: document.getElementById("notice"),
      loadAll: document.getElementById("load-all"),
      details: document.getElementById("details"),
      counts: document.getElementById("counts"),
      legend: document.getElementById("legend"),
      nodeFilters: document.getElementById("node-filters"),
      edgeFilters: document.getElementById("edge-filters"),
      search: document.getElementById("search"),
      searchResults: document.getElementById("search-results"),
      resetView: document.getElementById("reset-view"),
      clearFocus: document.getElementById("clear-focus"),
      debugPanel: document.getElementById("debug-panel"),
      debugStats: document.getElementById("debug-stats"),
    };

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function rgba(hex, alpha) {
      const clean = String(hex || "#94a3b8").replace("#", "");
      const expanded =
        clean.length === 3 ? clean.split("").map(char => char + char).join("") : clean;
      const value = Number.parseInt(expanded, 16);
      const r = (value >> 16) & 255;
      const g = (value >> 8) & 255;
      const b = value & 255;
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function truncateLabel(value, maxLength = 28) {
      const text = String(value || "");
      return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
    }

    function endpointId(endpoint) {
      return typeof endpoint === "object" && endpoint !== null ? endpoint.id : endpoint;
    }

    function nodeRadius(node) {
      return Math.sqrt(Math.max(1, 1 + Math.min(node.degree || 0, 18) * 0.7)) * NODE_REL_SIZE;
    }

    // Reconstroi o subconjunto visivel a partir dos filtros e do modo hubs-only.
    function buildVisibleData() {
      const nodeSet = new Set();
      for (const node of rawNodes) {
        if (!activeNodeKinds.has(node.kind)) continue;
        if (!state.showAll && summaryFocusIds.size && !summaryFocusIds.has(node.id)) continue;
        nodeSet.add(node.id);
      }
      const links = [];
      for (const edge of rawEdges) {
        if (!activeEdgeKinds.has(edge.kind)) continue;
        if (nodeSet.has(edge.source) && nodeSet.has(edge.target)) {
          links.push({ source: edge.source, target: edge.target, kind: edge.kind });
        }
      }
      const nodes = rawNodes.filter(node => nodeSet.has(node.id));
      state.visibleNodeCount = nodes.length;
      state.visibleEdgeCount = links.length;
      return { nodes, links, nodeSet };
    }

    // Foco = correspondencias de busca (ou selecao) + vizinhanca 1-hop.
    function refreshFocus(nodeSet) {
      const matched = new Set();
      const focus = new Set();
      if (state.search) {
        for (const node of rawNodes) {
          if (nodeSet && !nodeSet.has(node.id)) continue;
          const label = String(node.label || "").toLowerCase();
          if (node.id.toLowerCase().includes(state.search) || label.includes(state.search)) {
            matched.add(node.id);
            focus.add(node.id);
          }
        }
      } else if (state.selectedId && (!nodeSet || nodeSet.has(state.selectedId))) {
        focus.add(state.selectedId);
      }
      for (const nodeId of focus.size ? [...focus] : []) {
        for (const neighbor of adjacency.get(nodeId) || []) {
          if (!nodeSet || nodeSet.has(neighbor.id)) focus.add(neighbor.id);
        }
      }
      state.matchedIds = matched;
      state.focusIds = focus;
    }

    function colorForNode(node) {
      const base = colorByKind[node.kind] || "#94a3b8";
      let alpha = 0.9;
      if (state.focusIds.size && !state.focusIds.has(node.id)) {
        alpha = 0.1;
      } else if (!state.focusIds.size && viewer.hubs_only && !state.showAll && !highlightHubIds.has(node.id)) {
        alpha = 0.45;
      }
      if (state.matchedIds.has(node.id)) alpha = 1;
      if (node.id === state.selectedId || node.id === state.hoveredId) alpha = 1;
      return rgba(base, alpha);
    }

    function colorForLink(link) {
      const style = edgeStyleByKind[link.kind] || { color: "#94a3b8", alpha: 0.18 };
      let alpha = style.alpha;
      if (state.focusIds.size) {
        const inFocus = state.focusIds.has(endpointId(link.source)) && state.focusIds.has(endpointId(link.target));
        alpha = inFocus ? Math.min(0.62, style.alpha + 0.24) : 0.03;
      }
      return rgba(style.color, alpha);
    }

    function widthForLink(link) {
      return (edgeStyleByKind[link.kind] || { width: 1 }).width;
    }

    function drawNodeDecorations(node, ctx, scale) {
      if (node.x === undefined || node.y === undefined) return;
      const dimmed = state.focusIds.size && !state.focusIds.has(node.id);
      const radius = nodeRadius(node);
      const isHub = highlightHubIds.has(node.id);

      if (isHub && !dimmed && scale >= 0.7) {
        ctx.beginPath();
        ctx.lineWidth = 1 / scale;
        ctx.strokeStyle = rgba("#ffffff", 0.2);
        ctx.arc(node.x, node.y, radius + 2.4, 0, Math.PI * 2);
        ctx.stroke();
      }

      const forced =
        node.id === state.selectedId ||
        node.id === state.hoveredId ||
        state.matchedIds.has(node.id);
      const shouldLabel = scale >= focusLabelZoomThreshold && (forced || scale >= contextLabelZoomThreshold);
      if (!shouldLabel || (dimmed && !forced)) return;

      const strong = forced;
      const text = truncateLabel(node.label || node.id, strong ? 34 : 22);
      const fontSize = (strong ? 12 : 11) / scale;
      ctx.font = `${fontSize}px "Avenir Next", "Segoe UI", sans-serif`;
      const padX = 6 / scale;
      const width = ctx.measureText(text).width + padX * 2;
      const height = (strong ? 20 : 18) / scale;
      const labelX = node.x + radius + 3 / scale;
      const labelY = node.y - height - 3 / scale;
      ctx.fillStyle = strong ? "rgba(9, 13, 18, 0.92)" : "rgba(13, 17, 23, 0.82)";
      ctx.strokeStyle = strong ? "rgba(122, 162, 247, 0.34)" : "rgba(148, 163, 184, 0.16)";
      ctx.lineWidth = 1 / scale;
      if (ctx.roundRect) {
        ctx.beginPath();
        ctx.roundRect(labelX, labelY, width, height, 8 / scale);
        ctx.fill();
        ctx.stroke();
      } else {
        ctx.fillRect(labelX, labelY, width, height);
      }
      ctx.fillStyle = strong ? "#f8fbff" : "#dbe5f0";
      ctx.textBaseline = "middle";
      ctx.fillText(text, labelX + padX, labelY + height / 2);
    }

    const Graph = ForceGraph()(elements.graph)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeId("id")
      .nodeRelSize(NODE_REL_SIZE)
      .nodeVal(node => 1 + Math.min(node.degree || 0, 18) * 0.7)
      .nodeColor(colorForNode)
      .nodeLabel(node => escapeHtml(node.label || node.id))
      .linkColor(colorForLink)
      .linkWidth(widthForLink)
      .nodeCanvasObjectMode(() => "after")
      .nodeCanvasObject(drawNodeDecorations)
      .onNodeClick(node => selectNode(node.id))
      .onNodeHover(node => {
        state.hoveredId = node ? node.id : null;
        elements.graph.classList.toggle("dragging", false);
      })
      .onBackgroundClick(() => clearSelection());

    if (typeof Graph.minZoom === "function") Graph.minZoom(minZoom);
    if (typeof Graph.maxZoom === "function") Graph.maxZoom(maxZoom);

    // Guardrail de fisica: grafos grandes recebem menos ticks para nao travar.
    const heavy = rawNodes.length > physicsThreshold;
    Graph.cooldownTicks(heavy ? 80 : 220).cooldownTime(heavy ? 4000 : 9000);
    graph.viewer = graph.viewer || {};
    graph.viewer.layout_mode = heavy ? "force-graph-light" : "force-graph";
    Graph.d3Force("charge").strength(heavy ? -80 : -140);
    if (Graph.d3Force("link")) {
      Graph.d3Force("link").distance(link => (link.kind === "contains" ? 26 : 62));
    }

    let didInitialFit = false;
    const fitPadding = 48;
    Graph.onEngineStop(() => {
      if (!didInitialFit) {
        didInitialFit = true;
        Graph.zoomToFit(500, fitPadding);
      }
    });

    function applyData() {
      const data = buildVisibleData();
      refreshFocus(data.nodeSet);
      Graph.graphData({ nodes: data.nodes, links: data.links });
      renderCounters();
      renderDebug();
    }

    function refreshPaint() {
      // Re-emite os acessores para reavaliar cores/labels sem reheatar o layout.
      Graph.nodeColor(colorForNode).linkColor(colorForLink);
    }

    function selectNode(nodeId) {
      if (!nodesById.has(nodeId)) return;
      state.selectedId = nodeId;
      state.search = "";
      elements.search.value = "";
      renderDetails(nodesById.get(nodeId));
      refreshFocus(buildVisibleData().nodeSet);
      refreshPaint();
      const node = nodesById.get(nodeId);
      if (node && node.x !== undefined) {
        Graph.centerAt(node.x, node.y, 600);
        Graph.zoom(Math.max(Graph.zoom(), 1.6), 600);
      }
      renderCounters();
    }

    function clearSelection() {
      state.selectedId = null;
      renderDetails(null);
      refreshFocus(buildVisibleData().nodeSet);
      refreshPaint();
      renderCounters();
    }

    function renderLegend() {
      elements.legend.innerHTML = nodeKinds
        .map(kind => {
          const total = (nodeIdsByKind.get(kind) || []).length;
          return `
            <div class="legend-item">
              <span><span class="legend-swatch" style="background:${colorByKind[kind] || "#94a3b8"}"></span> ${escapeHtml(kind)}</span>
              <span class="muted">${total}</span>
            </div>
          `;
        })
        .join("");
    }

    function renderFilterGroup(container, values, activeSet) {
      container.innerHTML = values
        .map(
          value => `
            <label class="filter-item">
              <input type="checkbox" data-value="${escapeHtml(value)}" checked>
              <span>${escapeHtml(value)}</span>
            </label>
          `
        )
        .join("");
      container.querySelectorAll("input").forEach(input => {
        input.addEventListener("change", () => {
          if (input.checked) {
            activeSet.add(input.dataset.value);
          } else {
            activeSet.delete(input.dataset.value);
          }
          applyData();
        });
      });
    }

    function renderCounters() {
      const totalNodes = viewer.node_count || graph.metrics?.node_count || rawNodes.length;
      const totalEdges = viewer.edge_count || graph.metrics?.edge_count || rawEdges.length;
      const summary = [
        ["Nos visiveis", `${state.visibleNodeCount} / ${totalNodes}`],
        ["Arestas visiveis", `${state.visibleEdgeCount} / ${totalEdges}`],
        ["Layout", graph.viewer.layout_mode || viewer.layout_mode || "auto"],
      ];
      elements.counts.innerHTML = summary
        .map(([label, value]) => `<div class="metric-row"><span class="muted">${label}</span><span>${escapeHtml(value)}</span></div>`)
        .join("");
      if (state.search) {
        elements.searchResults.textContent = state.matchedIds.size
          ? `${state.matchedIds.size} correspondencia(s) em foco`
          : "Nenhuma correspondencia encontrada";
      } else if (state.selectedId) {
        elements.searchResults.textContent = "Foco local ativo";
      } else if (!state.showAll && viewer.hubs_only) {
        elements.searchResults.textContent = "Resumo por hubs ativo";
      } else {
        elements.searchResults.textContent = "";
      }
    }

    function renderDebug() {
      if (!debugEnabled) return;
      elements.debugPanel.hidden = false;
      elements.debugStats.innerHTML = [
        ["Bootstrap", `${perf.bootstrapMs.toFixed(1)} ms`],
        ["Layout mode", graph.viewer.layout_mode || viewer.layout_mode || "auto"],
        ["Nos visiveis", `${state.visibleNodeCount}`],
        ["Arestas visiveis", `${state.visibleEdgeCount}`],
        ["Zoom", `${Graph.zoom().toFixed(2)}`],
        ["Foco", state.focusIds.size ? `${state.focusIds.size} nos` : "inativo"],
      ]
        .map(([label, value]) => `<div class="metric-row"><span class="muted">${escapeHtml(label)}</span><span>${escapeHtml(value)}</span></div>`)
        .join("");
    }

    function renderDetails(node) {
      if (!node) {
        elements.details.innerHTML = '<span class="muted">Selecione um no para focar o subgrafo local.</span>';
        return;
      }
      const neighbors = (adjacency.get(node.id) || [])
        .map(item => ({ edgeKind: item.kind, node: nodesById.get(item.id) }))
        .filter(item => item.node)
        .slice(0, 20);
      const lines = Array.isArray(node.lines) ? node.lines.join("-") : "-";
      const items = neighbors
        .map(
          item => `
            <li>
              <span class="node-link" data-node="${escapeHtml(item.node.id)}">${escapeHtml(truncateLabel(item.node.label || item.node.id, 42))}</span>
              <span class="muted">(${escapeHtml(item.edgeKind)})</span>
            </li>
          `
        )
        .join("");
      elements.details.innerHTML = `
        <div class="details-list">
          <div><strong>${escapeHtml(node.label || node.id)}</strong></div>
          <div><span class="muted">id</span><br><code>${escapeHtml(node.id)}</code></div>
          <div><span class="muted">kind</span><br>${escapeHtml(node.kind)}</div>
          <div><span class="muted">file</span><br>${escapeHtml(node.file_path || "-")}</div>
          <div><span class="muted">lines</span><br>${escapeHtml(lines)}</div>
          <div><span class="muted">degree</span><br>${escapeHtml(node.degree || 0)}</div>
          <div class="button-row"><button id="copy-id">Copiar id</button></div>
          <div><span class="muted">Vizinhos</span><ul>${items || "<li>-</li>"}</ul></div>
        </div>
      `;
      const copyButton = document.getElementById("copy-id");
      if (copyButton) {
        copyButton.onclick = async () => {
          try {
            await navigator.clipboard.writeText(node.id);
          } catch (_) {}
        };
      }
      elements.details.querySelectorAll("[data-node]").forEach(element => {
        element.addEventListener("click", () => selectNode(element.dataset.node));
      });
    }

    function resizeGraph() {
      Graph.width(elements.graph.clientWidth || 1).height(elements.graph.clientHeight || 1);
    }

    elements.search.addEventListener("input", () => {
      state.search = elements.search.value.trim().toLowerCase();
      state.selectedId = null;
      refreshFocus(buildVisibleData().nodeSet);
      refreshPaint();
      renderCounters();
      renderDebug();
    });

    elements.resetView.addEventListener("click", () => Graph.zoomToFit(500, fitPadding));

    elements.clearFocus.addEventListener("click", () => {
      elements.search.value = "";
      state.search = "";
      state.hoveredId = null;
      clearSelection();
    });

    elements.loadAll.addEventListener("click", () => {
      state.showAll = true;
      elements.notice.hidden = true;
      elements.loadAll.hidden = true;
      didInitialFit = false;
      applyData();
    });

    window.addEventListener("resize", resizeGraph);

    elements.title.textContent = graph.workspace_repo || "Atlas Graph";
    elements.subtitle.textContent = `${graph.generated_at || ""} · ${viewer.node_count || graph.metrics?.node_count || rawNodes.length} nos`;
    renderFilterGroup(elements.nodeFilters, nodeKinds, activeNodeKinds);
    renderFilterGroup(elements.edgeFilters, edgeKinds, activeEdgeKinds);
    renderLegend();
    if (viewer.hubs_only) {
      elements.notice.hidden = false;
      elements.notice.textContent = viewer.notice || "Resumo por hubs ativo para manter o mapa fluido.";
      elements.loadAll.hidden = false;
    }
    if (debugEnabled) {
      elements.debugPanel.hidden = false;
    }

    resizeGraph();
    applyData();
    renderDetails(null);
    perf.bootstrapMs = performance.now() - bootstrapStartedAt;
    renderDebug();
  </script>
</body>
</html>
"""


def write_graph_html(graph: dict, index_dir: Path) -> Path:
    """Gera `graph.html` autocontido com o grafo e o force-graph embutidos."""
    graph_for_view = json.loads(json.dumps(graph))
    metrics = graph_for_view.get("metrics", {})
    node_count = len(graph_for_view.get("nodes", []))
    edge_count = len(graph_for_view.get("edges", []))
    top_hub_ids = [item["id"] for item in metrics.get("top_hubs", [])]

    focus_ids = set()
    if node_count > GRAPH_VIEWER_MAX_FULL_NODES:
        focus_ids.update(top_hub_ids)
        for edge in graph_for_view.get("edges", []):
            if edge["source"] in focus_ids:
                focus_ids.add(edge["target"])
            if edge["target"] in focus_ids:
                focus_ids.add(edge["source"])

    existing_viewer = dict(graph_for_view.get("viewer") or {})
    existing_render_profile = dict(existing_viewer.get("render_profile") or {})
    graph_for_view["viewer"] = {
        **existing_viewer,
        "node_count": metrics.get("node_count", node_count),
        "edge_count": metrics.get("edge_count", edge_count),
        "layout_mode": "light-relaxed" if node_count <= 250 else "radial-seeded",
        "highlight_hub_ids": top_hub_ids,
        "render_profile": {
            **existing_render_profile,
            "label_mode": "focus-only",
            "pixel_ratio_cap": 1.5,
            "physics_threshold": 250,
            "label_zoom_threshold": 0.82,
            "focus_label_zoom_threshold": 0.34,
            "min_zoom": 0.18,
            "max_zoom": 10,
            "zoom_step_in": 1.12,
            "zoom_step_out": 0.9,
        },
        "hubs_only": node_count > GRAPH_VIEWER_MAX_FULL_NODES,
        "focus_node_ids": sorted(focus_ids) if node_count > GRAPH_VIEWER_MAX_FULL_NODES else [],
        "notice": (
            "Grafo grande: exibindo hubs e vizinhanca 1-hop por padrao."
            if node_count > GRAPH_VIEWER_MAX_FULL_NODES
            else ""
        ),
    }

    json_payload = json.dumps(graph_for_view, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    html = _HTML_TEMPLATE.replace("__FORCE_GRAPH_LIB__", _load_force_graph_lib())
    html = html.replace("__GRAPH_JSON__", json_payload)

    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    output_path = index_dir / GRAPH_HTML_FILENAME
    tmp_path = output_path.with_suffix(".html.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp_path, output_path)
    return output_path
