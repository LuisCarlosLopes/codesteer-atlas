import json
import os
from pathlib import Path

from codesteer_atlas.config import GRAPH_HTML_FILENAME, GRAPH_VIEWER_MAX_FULL_NODES

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
    canvas {
      position: relative;
      z-index: 1;
      display: block;
      width: 100%;
      height: 100vh;
      cursor: grab;
    }
    canvas.dragging { cursor: grabbing; }
    @media (max-width: 980px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar {
        max-height: 44vh;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }
      .stage,
      canvas { min-height: 56vh; height: 56vh; }
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
        <div id="details" class="details small muted">Selecione um nó para focar o subgrafo local.</div>
      </div>

      <div class="panel-block" id="debug-panel" hidden>
        <div class="section-title">Debug</div>
        <div id="debug-stats" class="metric-stack small"></div>
      </div>
    </aside>

    <main class="stage">
      <canvas id="graph"></canvas>
    </main>
  </div>

  <script id="graph-data" type="application/json">__GRAPH_JSON__</script>
  <script>
    const bootstrapStartedAt = performance.now();
    const graph = JSON.parse(document.getElementById("graph-data").textContent);
    const viewer = graph.viewer || {};
    const debugEnabled =
      /(?:\\?|&)debug=1\\b/.test(location.search) ||
      /(?:^|[#&])debug(?:=1)?\\b/.test(location.hash);
    const colorByKind = {
      file: "#7aa2f7",
      doc: "#f1c27d",
      symbol: "#76c7b7",
      section: "#ff9f6e",
      rationale: "#b392f0",
    };
    const edgeStyleByKind = {
      contains: { color: "#5b6777", alpha: 0.12, width: 0.8 },
      imports: { color: "#7aa2f7", alpha: 0.30, width: 1.05 },
      links_to: { color: "#f1c27d", alpha: 0.34, width: 1.12 },
      cites: { color: "#76c7b7", alpha: 0.38, width: 1.20 },
      annotates: { color: "#b392f0", alpha: 0.34, width: 1.08 },
    };
    const canvas = document.getElementById("graph");
    const ctx = canvas.getContext("2d");
    const title = document.getElementById("title");
    const subtitle = document.getElementById("subtitle");
    const notice = document.getElementById("notice");
    const loadAllButton = document.getElementById("load-all");
    const details = document.getElementById("details");
    const counts = document.getElementById("counts");
    const legend = document.getElementById("legend");
    const nodeFilters = document.getElementById("node-filters");
    const edgeFilters = document.getElementById("edge-filters");
    const search = document.getElementById("search");
    const searchResults = document.getElementById("search-results");
    const resetView = document.getElementById("reset-view");
    const clearFocus = document.getElementById("clear-focus");
    const debugPanel = document.getElementById("debug-panel");
    const debugStats = document.getElementById("debug-stats");
    const physicsThreshold = viewer.render_profile?.physics_threshold || 250;
    const pixelRatioCap = viewer.render_profile?.pixel_ratio_cap || 1.5;
    const focusLabelZoomThreshold = viewer.render_profile?.focus_label_zoom_threshold || 0.34;
    const contextLabelZoomThreshold = viewer.render_profile?.label_zoom_threshold || 0.82;
    const minZoom = viewer.render_profile?.min_zoom || 0.18;
    const maxZoom = viewer.render_profile?.max_zoom || 10;
    const zoomStepIn = viewer.render_profile?.zoom_step_in || 1.12;
    const zoomStepOut = viewer.render_profile?.zoom_step_out || 0.9;
    const nodes = graph.nodes.map(node => ({ ...node, x: 0, y: 0, vx: 0, vy: 0 }));
    const edges = graph.edges.slice();
    const nodesById = new Map(nodes.map(node => [node.id, node]));
    const nodeKinds = [...new Set(nodes.map(node => node.kind))];
    const edgeKinds = [...new Set(edges.map(edge => edge.kind))];
    const activeNodeKinds = new Set(nodeKinds);
    const activeEdgeKinds = new Set(edgeKinds);
    const summaryFocusIds = new Set(viewer.focus_node_ids || []);
    const highlightHubIds = new Set(viewer.highlight_hub_ids || []);
    const nodeIdsByKind = new Map(nodeKinds.map(kind => [kind, []]));
    const edgesByKind = new Map(edgeKinds.map(kind => [kind, []]));
    const adjacency = new Map(nodes.map(node => [node.id, []]));
    const state = {
      scale: 1,
      offsetX: 0,
      offsetY: 0,
      draggingNode: null,
      panning: false,
      lastX: 0,
      lastY: 0,
      selectedId: null,
      hoveredId: null,
      search: "",
      showAll: !viewer.hubs_only,
    };
    const perf = {
      bootstrapMs: 0,
      layoutMs: 0,
      averageDrawMs: 0,
      lastDrawMs: 0,
      draws: 0,
    };
    const visibleCache = {
      dirty: true,
      nodeIds: [],
      nodeSet: new Set(),
      edges: [],
      matchedNodeIds: new Set(),
      focusNodeIds: new Set(),
      hubNodeIds: new Set(),
      searchResultCount: 0,
    };

    for (const node of nodes) {
      nodeIdsByKind.get(node.kind).push(node.id);
    }
    for (const edge of edges) {
      edgesByKind.get(edge.kind).push(edge);
      if (adjacency.has(edge.source)) adjacency.get(edge.source).push({ id: edge.target, kind: edge.kind });
      if (adjacency.has(edge.target)) adjacency.get(edge.target).push({ id: edge.source, kind: edge.kind });
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function rgba(hex, alpha) {
      const clean = hex.replace("#", "");
      const expanded =
        clean.length === 3
          ? clean
              .split("")
              .map(char => char + char)
              .join("")
          : clean;
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

    function invalidateVisibility() {
      visibleCache.dirty = true;
    }

    function radiusFor(node) {
      const degree = Math.min(node.degree || 0, 18);
      const base = node.kind === "file" || node.kind === "doc" ? 4.1 : 3.2;
      return base + degree * 0.16;
    }

    function setupCanvas() {
      const ratio = Math.min(window.devicePixelRatio || 1, pixelRatioCap);
      canvas.width = Math.max(1, Math.floor(canvas.clientWidth * ratio));
      canvas.height = Math.max(1, Math.floor(canvas.clientHeight * ratio));
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    function screenPoint(node, width, height) {
      return {
        x: width / 2 + node.x * state.scale + state.offsetX,
        y: height / 2 + node.y * state.scale + state.offsetY,
      };
    }

    function fitView() {
      const visibility = currentVisibility();
      if (!visibility.nodeIds.length) {
        return;
      }
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      for (const nodeId of visibility.nodeIds) {
        const node = nodesById.get(nodeId);
        minX = Math.min(minX, node.x);
        maxX = Math.max(maxX, node.x);
        minY = Math.min(minY, node.y);
        maxY = Math.max(maxY, node.y);
      }
      const spanX = Math.max(maxX - minX, 140);
      const spanY = Math.max(maxY - minY, 140);
      const padding = Math.min(width, height) * 0.16;
      state.scale = Math.max(0.22, Math.min(1.45, Math.min((width - padding) / spanX, (height - padding) / spanY)));
      state.offsetX = -((minX + maxX) / 2) * state.scale;
      state.offsetY = -((minY + maxY) / 2) * state.scale;
    }

    function centerOnNode(nodeId) {
      const node = nodesById.get(nodeId);
      if (!node) {
        return;
      }
      state.scale = Math.max(state.scale, 0.96);
      state.offsetX = -(node.x * state.scale);
      state.offsetY = -(node.y * state.scale);
    }

    function currentVisibility() {
      if (!visibleCache.dirty) {
        return visibleCache;
      }

      const nodeIds = [];
      for (const kind of nodeKinds) {
        if (!activeNodeKinds.has(kind)) {
          continue;
        }
        for (const nodeId of nodeIdsByKind.get(kind) || []) {
          if (!state.showAll && summaryFocusIds.size && !summaryFocusIds.has(nodeId)) {
            continue;
          }
          nodeIds.push(nodeId);
        }
      }

      const nodeSet = new Set(nodeIds);
      const visibleEdges = [];
      for (const kind of edgeKinds) {
        if (!activeEdgeKinds.has(kind)) {
          continue;
        }
        for (const edge of edgesByKind.get(kind) || []) {
          if (nodeSet.has(edge.source) && nodeSet.has(edge.target)) {
            visibleEdges.push(edge);
          }
        }
      }

      const matchedNodeIds = new Set();
      const focusNodeIds = new Set();
      const hubNodeIds = new Set();
      if (state.search) {
        for (const nodeId of nodeIds) {
          const node = nodesById.get(nodeId);
          const label = String(node.label || "").toLowerCase();
          if (nodeId.toLowerCase().includes(state.search) || label.includes(state.search)) {
            matchedNodeIds.add(nodeId);
            focusNodeIds.add(nodeId);
          }
        }
        for (const nodeId of matchedNodeIds) {
          for (const neighbor of adjacency.get(nodeId) || []) {
            if (nodeSet.has(neighbor.id)) {
              focusNodeIds.add(neighbor.id);
            }
          }
        }
      } else if (state.selectedId && nodeSet.has(state.selectedId)) {
        focusNodeIds.add(state.selectedId);
        for (const neighbor of adjacency.get(state.selectedId) || []) {
          if (nodeSet.has(neighbor.id)) {
            focusNodeIds.add(neighbor.id);
          }
        }
      }

      for (const nodeId of highlightHubIds) {
        if (nodeSet.has(nodeId)) {
          hubNodeIds.add(nodeId);
        }
      }

      visibleCache.nodeIds = nodeIds;
      visibleCache.nodeSet = nodeSet;
      visibleCache.edges = visibleEdges;
      visibleCache.matchedNodeIds = matchedNodeIds;
      visibleCache.focusNodeIds = focusNodeIds;
      visibleCache.hubNodeIds = hubNodeIds;
      visibleCache.searchResultCount = matchedNodeIds.size;
      visibleCache.dirty = false;
      return visibleCache;
    }

    function seedLayout() {
      const ordered = nodes
        .slice()
        .sort((left, right) => (right.degree || 0) - (left.degree || 0) || left.id.localeCompare(right.id));
      const kindRing = new Map(nodeKinds.map((kind, index) => [kind, 140 + index * 38]));
      const angleStep = (Math.PI * 2) / Math.max(ordered.length, 1);
      ordered.forEach((node, index) => {
        const ring = kindRing.get(node.kind) + Math.min(node.degree || 0, 12) * 3;
        const angle = angleStep * index;
        const driftX = ((index % 7) - 3) * 4;
        const driftY = ((index % 11) - 5) * 3;
        node.x = Math.cos(angle) * ring + driftX;
        node.y = Math.sin(angle) * ring + driftY;
        node.vx = 0;
        node.vy = 0;
      });
    }

    function runInitialLayout() {
      const startedAt = performance.now();
      seedLayout();
      let iterations = 0;
      if (nodes.length <= 90) {
        iterations = 42;
      } else if (nodes.length <= physicsThreshold) {
        iterations = 18;
      }
      for (let iter = 0; iter < iterations; iter++) {
        const repulsion = 1800 / (1 + iter * 0.08);
        for (let i = 0; i < nodes.length; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < nodes.length; j++) {
            const b = nodes[j];
            let dx = a.x - b.x;
            let dy = a.y - b.y;
            const dist2 = Math.max(dx * dx + dy * dy, 0.5);
            const dist = Math.sqrt(dist2);
            const force = repulsion / dist2;
            dx /= dist;
            dy /= dist;
            a.vx += dx * force;
            a.vy += dy * force;
            b.vx -= dx * force;
            b.vy -= dy * force;
          }
        }
        for (const edge of edges) {
          const source = nodesById.get(edge.source);
          const target = nodesById.get(edge.target);
          if (!source || !target) {
            continue;
          }
          const dx = target.x - source.x;
          const dy = target.y - source.y;
          const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01);
          const desired = edge.kind === "contains" ? 56 : 104;
          const force = (dist - desired) * 0.0045;
          source.vx += dx * force;
          source.vy += dy * force;
          target.vx -= dx * force;
          target.vy -= dy * force;
        }
        for (const node of nodes) {
          node.vx += -node.x * 0.00075;
          node.vy += -node.y * 0.00075;
          node.x += node.vx;
          node.y += node.vy;
          node.vx *= 0.82;
          node.vy *= 0.82;
        }
      }
      perf.layoutMs = performance.now() - startedAt;
      graph.viewer.layout_mode = iterations ? (nodes.length <= 90 ? "relaxed" : "light-relaxed") : "radial-seeded";
      return iterations > 0;
    }

    function renderLegend() {
      legend.innerHTML = nodeKinds
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
          invalidateVisibility();
          draw();
        });
      });
    }

    function renderCounters(visibility) {
      const totalNodes = viewer.node_count || graph.metrics?.node_count || nodes.length;
      const totalEdges = viewer.edge_count || graph.metrics?.edge_count || edges.length;
      const summary = [
        ["Nos visiveis", `${visibility.nodeIds.length} / ${totalNodes}`],
        ["Arestas visiveis", `${visibility.edges.length} / ${totalEdges}`],
        ["Layout", graph.viewer.layout_mode || viewer.layout_mode || "auto"],
      ];
      counts.innerHTML = summary
        .map(([label, value]) => `<div class="metric-row"><span class="muted">${label}</span><span>${escapeHtml(value)}</span></div>`)
        .join("");
      if (state.search) {
        searchResults.textContent = visibility.searchResultCount
          ? `${visibility.searchResultCount} correspondencia(s) em foco`
          : "Nenhuma correspondencia encontrada";
      } else if (state.selectedId) {
        searchResults.textContent = "Foco local ativo";
      } else if (!state.showAll && viewer.hubs_only) {
        searchResults.textContent = "Resumo por hubs ativo";
      } else {
        searchResults.textContent = "";
      }
    }

    function renderDebug(visibility) {
      if (!debugEnabled) {
        return;
      }
      debugPanel.hidden = false;
      debugStats.innerHTML = [
        ["Bootstrap", `${perf.bootstrapMs.toFixed(1)} ms`],
        ["Layout inicial", `${perf.layoutMs.toFixed(1)} ms`],
        ["Draw medio", `${perf.averageDrawMs.toFixed(2)} ms`],
        ["Ultimo draw", `${perf.lastDrawMs.toFixed(2)} ms`],
        ["Layout mode", graph.viewer.layout_mode || viewer.layout_mode || "auto"],
        ["Pixel ratio", `${Math.min(window.devicePixelRatio || 1, pixelRatioCap).toFixed(2)}`],
        ["Foco", visibility.focusNodeIds.size ? `${visibility.focusNodeIds.size} nos` : "inativo"],
      ]
        .map(([label, value]) => `<div class="metric-row"><span class="muted">${escapeHtml(label)}</span><span>${escapeHtml(value)}</span></div>`)
        .join("");
    }

    function renderDetails(node) {
      if (!node) {
        details.innerHTML = '<span class="muted">Selecione um no para focar o subgrafo local.</span>';
        return;
      }
      const neighbors = (adjacency.get(node.id) || [])
        .map(item => ({
          edgeKind: item.kind,
          node: nodesById.get(item.id),
        }))
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
      details.innerHTML = `
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
      details.querySelectorAll("[data-node]").forEach(element => {
        element.addEventListener("click", () => selectNode(element.dataset.node));
      });
    }

    function selectNode(nodeId) {
      if (!nodesById.has(nodeId)) {
        return;
      }
      state.selectedId = nodeId;
      renderDetails(nodesById.get(nodeId));
      centerOnNode(nodeId);
      invalidateVisibility();
      draw();
    }

    function clearSelection() {
      state.selectedId = null;
      renderDetails(null);
      invalidateVisibility();
      draw();
    }

    function nodeVisual(nodeId, visibility) {
      const isSelected = state.selectedId === nodeId;
      const isHovered = state.hoveredId === nodeId;
      const isMatched = visibility.matchedNodeIds.has(nodeId);
      const inFocus = visibility.focusNodeIds.has(nodeId);
      const isHub = visibility.hubNodeIds.has(nodeId);
      let alpha = 0.88;
      let emphasis = 0;
      if (visibility.focusNodeIds.size) {
        alpha = inFocus ? 0.92 : 0.12;
        emphasis = inFocus ? 1 : 0;
      } else if (viewer.hubs_only && !state.showAll && !isHub) {
        alpha = 0.18;
      }
      if (isMatched) {
        alpha = 1;
        emphasis = 2;
      }
      if (isHub) {
        alpha = Math.max(alpha, 0.84);
      }
      if (isHovered) {
        alpha = 1;
        emphasis = Math.max(emphasis, 2);
      }
      if (isSelected) {
        alpha = 1;
        emphasis = 3;
      }
      return { alpha, emphasis, isHub, isMatched };
    }

    function labelNodeIds(visibility) {
      const ids = new Set();
      if (state.selectedId && visibility.nodeSet.has(state.selectedId)) {
        ids.add(state.selectedId);
      }
      if (state.hoveredId && visibility.nodeSet.has(state.hoveredId)) {
        ids.add(state.hoveredId);
      }
      if (state.scale >= contextLabelZoomThreshold) {
        for (const nodeId of visibility.matchedNodeIds) {
          ids.add(nodeId);
        }
        for (const nodeId of visibility.hubNodeIds) {
          ids.add(nodeId);
        }
      }
      return ids;
    }

    function drawLabel(node, x, y, strong) {
      const text = truncateLabel(node.label || node.id, strong ? 34 : 22);
      ctx.font = strong ? '12px "Avenir Next", "Segoe UI", sans-serif' : '11px "Avenir Next", "Segoe UI", sans-serif';
      const metrics = ctx.measureText(text);
      const width = metrics.width + 12;
      const height = strong ? 20 : 18;
      const labelX = x + 10;
      const labelY = y - height - 8;
      ctx.fillStyle = strong ? "rgba(9, 13, 18, 0.92)" : "rgba(13, 17, 23, 0.82)";
      ctx.strokeStyle = strong ? "rgba(122, 162, 247, 0.34)" : "rgba(148, 163, 184, 0.16)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(labelX, labelY, width, height, 9);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = strong ? "#f8fbff" : "#dbe5f0";
      ctx.textBaseline = "middle";
      ctx.fillText(text, labelX + 6, labelY + height / 2 + 0.3);
    }

    function draw() {
      const drawStartedAt = performance.now();
      const visibility = currentVisibility();
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      ctx.clearRect(0, 0, width, height);

      renderCounters(visibility);

      for (const edge of visibility.edges) {
        const source = nodesById.get(edge.source);
        const target = nodesById.get(edge.target);
        if (!source || !target) {
          continue;
        }
        const sourceVisual = nodeVisual(edge.source, visibility);
        const targetVisual = nodeVisual(edge.target, visibility);
        const style = edgeStyleByKind[edge.kind] || { color: "#94a3b8", alpha: 0.18, width: 1 };
        const edgeAlpha = visibility.focusNodeIds.size
          ? sourceVisual.alpha * targetVisual.alpha * 0.72
          : style.alpha;
        const a = screenPoint(source, width, height);
        const b = screenPoint(target, width, height);
        ctx.beginPath();
        ctx.strokeStyle = rgba(style.color, Math.max(0.04, Math.min(0.58, edgeAlpha)));
        ctx.lineWidth = style.width + (sourceVisual.emphasis && targetVisual.emphasis ? 0.3 : 0);
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }

      for (const nodeId of visibility.nodeIds) {
        const node = nodesById.get(nodeId);
        const point = screenPoint(node, width, height);
        const radius = radiusFor(node);
        const visual = nodeVisual(nodeId, visibility);
        if (visual.emphasis >= 2) {
          ctx.beginPath();
          ctx.fillStyle = rgba(colorByKind[node.kind] || "#94a3b8", visual.emphasis === 3 ? 0.18 : 0.12);
          ctx.arc(point.x, point.y, radius + (visual.emphasis === 3 ? 8 : 6), 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.beginPath();
        ctx.fillStyle = rgba(colorByKind[node.kind] || "#94a3b8", visual.alpha);
        ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
        ctx.fill();
        if (visual.isHub && state.scale >= 0.70) {
          ctx.beginPath();
          ctx.lineWidth = 1;
          ctx.strokeStyle = rgba("#ffffff", 0.18);
          ctx.arc(point.x, point.y, radius + 2.2, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      const shouldDrawContextLabels = state.scale >= contextLabelZoomThreshold;
      const shouldDrawFocusLabels = state.scale >= focusLabelZoomThreshold;
      if (shouldDrawFocusLabels) {
        const labels = labelNodeIds(visibility);
        if (labels.size || shouldDrawContextLabels) {
          for (const nodeId of labels) {
            const node = nodesById.get(nodeId);
            if (!node) {
              continue;
            }
            const point = screenPoint(node, width, height);
            const strong = nodeId === state.selectedId || nodeId === state.hoveredId || visibility.matchedNodeIds.has(nodeId);
            drawLabel(node, point.x, point.y, strong);
          }
        }
      }

      perf.lastDrawMs = performance.now() - drawStartedAt;
      perf.draws += 1;
      perf.averageDrawMs += (perf.lastDrawMs - perf.averageDrawMs) / perf.draws;
      renderDebug(visibility);
    }

    function nodeAt(clientX, clientY) {
      const visibility = currentVisibility();
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      for (let index = visibility.nodeIds.length - 1; index >= 0; index -= 1) {
        const nodeId = visibility.nodeIds[index];
        const node = nodesById.get(nodeId);
        const point = screenPoint(node, width, height);
        const radius = radiusFor(node) + 2;
        if ((clientX - point.x) ** 2 + (clientY - point.y) ** 2 <= radius ** 2) {
          return node;
        }
      }
      return null;
    }

    canvas.addEventListener("mousedown", event => {
      state.lastX = event.offsetX;
      state.lastY = event.offsetY;
      const node = nodeAt(event.offsetX, event.offsetY);
      if (node) {
        state.draggingNode = node;
        state.hoveredId = node.id;
        selectNode(node.id);
      } else {
        state.panning = true;
        canvas.classList.add("dragging");
      }
    });

    canvas.addEventListener("mousemove", event => {
      const dx = event.offsetX - state.lastX;
      const dy = event.offsetY - state.lastY;
      state.lastX = event.offsetX;
      state.lastY = event.offsetY;
      if (state.draggingNode) {
        state.draggingNode.x += dx / state.scale;
        state.draggingNode.y += dy / state.scale;
        draw();
        return;
      }
      if (state.panning) {
        state.offsetX += dx;
        state.offsetY += dy;
        draw();
        return;
      }
      const hovered = nodeAt(event.offsetX, event.offsetY);
      const hoveredId = hovered ? hovered.id : null;
      if (hoveredId !== state.hoveredId) {
        state.hoveredId = hoveredId;
        draw();
      }
    });

    canvas.addEventListener("mouseleave", () => {
      if (state.hoveredId !== null) {
        state.hoveredId = null;
        draw();
      }
    });

    window.addEventListener("mouseup", () => {
      state.draggingNode = null;
      state.panning = false;
      canvas.classList.remove("dragging");
    });

    canvas.addEventListener(
      "wheel",
      event => {
        event.preventDefault();
        const width = canvas.clientWidth || 1;
        const height = canvas.clientHeight || 1;
        const worldX = (event.offsetX - width / 2 - state.offsetX) / state.scale;
        const worldY = (event.offsetY - height / 2 - state.offsetY) / state.scale;
        const nextScale = Math.max(
          minZoom,
          Math.min(maxZoom, state.scale * (event.deltaY < 0 ? zoomStepIn : zoomStepOut))
        );
        state.scale = nextScale;
        state.offsetX = event.offsetX - width / 2 - worldX * nextScale;
        state.offsetY = event.offsetY - height / 2 - worldY * nextScale;
        draw();
      },
      { passive: false }
    );

    canvas.addEventListener("click", event => {
      const node = nodeAt(event.offsetX, event.offsetY);
      if (node) {
        selectNode(node.id);
      }
    });

    search.addEventListener("input", () => {
      state.search = search.value.trim().toLowerCase();
      invalidateVisibility();
      draw();
    });

    resetView.addEventListener("click", () => {
      fitView();
      draw();
    });

    clearFocus.addEventListener("click", () => {
      search.value = "";
      state.search = "";
      state.hoveredId = null;
      clearSelection();
    });

    loadAllButton.addEventListener("click", () => {
      state.showAll = true;
      notice.hidden = true;
      loadAllButton.hidden = true;
      invalidateVisibility();
      fitView();
      draw();
    });

    window.addEventListener("resize", () => {
      setupCanvas();
      fitView();
      draw();
    });

    title.textContent = graph.workspace_repo || "Atlas Graph";
    subtitle.textContent = `${graph.generated_at || ""} · ${viewer.node_count || graph.metrics?.node_count || nodes.length} nos`;
    renderFilterGroup(nodeFilters, nodeKinds, activeNodeKinds);
    renderFilterGroup(edgeFilters, edgeKinds, activeEdgeKinds);
    renderLegend();
    if (viewer.hubs_only) {
      notice.hidden = false;
      notice.textContent = viewer.notice || "Resumo por hubs ativo para manter o mapa fluido.";
      loadAllButton.hidden = false;
    }
    if (debugEnabled) {
      debugPanel.hidden = false;
    }

    const usedRelaxation = runInitialLayout();
    if (!usedRelaxation && !viewer.hubs_only) {
      notice.hidden = false;
      notice.textContent =
        `Layout rapido aplicado (${nodes.length} nos); a fisica completa fica limitada a ${physicsThreshold} nos para evitar travamento.`;
    }
    setupCanvas();
    fitView();
    perf.bootstrapMs = performance.now() - bootstrapStartedAt;
    draw();
  </script>
</body>
</html>
"""


def write_graph_html(graph: dict, index_dir: Path) -> Path:
    """Gera `graph.html` autocontido com o grafo embutido em JSON."""
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
    html = _HTML_TEMPLATE.replace("__GRAPH_JSON__", json_payload)

    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    output_path = index_dir / GRAPH_HTML_FILENAME
    tmp_path = output_path.with_suffix(".html.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp_path, output_path)
    return output_path
