"""
Pacote de contexto por tarefa (`atlas_context`): orquestra grafo + brief + manifest
sob orçamento por seção, sem acordar embeddings nem varrer LanceDB.
"""

from __future__ import annotations

import json
import posixpath
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from codesteer_atlas.config import (
    CONTEXT_BUDGET_BY_SECTION,
    CONTEXT_RESPONSE_MAX_CHARS,
)
from codesteer_atlas.graph import (
    _node_summary,
    _via_location,
    affected,
    explain,
    is_noise_hub,
    resolve_node,
)

# @MindContext: superfície MCP modelada pela tarefa do agente (edit/debug/review/understand)
# @MindDecision: cotas por seção + leftover + teto no serializador (DECISÃO-002); calls/git/diff degradam (DECISÃO-001/003)
# @MindTest: tests/test_context.py

VALID_INTENTS = ("edit", "debug", "review", "understand")
INTENT_SECTIONS = {
    "edit": ("symbol", "callers", "callees", "tests", "rationale"),
    "debug": ("symbol", "call_chain_to_entrypoints", "error_handling", "recent_history"),
    "review": ("diff", "impact", "tests", "adrs"),
    "understand": ("symbol", "layer", "neighbors", "brief_layer"),
}
_RELATION_KINDS = frozenset({"calls", "imports"})


def _serialized_len(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def _manifest_files(manifest) -> Dict[str, Any]:
    files = getattr(manifest, "files", None)
    if files is None and isinstance(manifest, dict):
        files = manifest.get("files")
    if isinstance(files, dict):
        return files
    if isinstance(files, (list, tuple, set)):
        return {path: "" for path in files}
    return {}


def _is_empty_section(value: Any) -> bool:
    return value in (None, [], {}, "")


def _trim_value(value: Any, max_chars: int) -> Tuple[Any, int]:
    if _serialized_len(value) <= max_chars:
        return value, 0
    if isinstance(value, list):
        items = list(value)
        omitted = 0
        while items and _serialized_len(items) > max_chars:
            items.pop()
            omitted += 1
        return items, omitted
    if isinstance(value, str):
        limit = max(0, max_chars - 2)
        return value[:limit], int(len(value) > limit)
    if isinstance(value, dict):
        trimmed = dict(value)
        omitted = 0
        for key, inner in sorted(
            trimmed.items(), key=lambda item: len(item[1]) if isinstance(item[1], str) else 0, reverse=True
        ):
            if not isinstance(inner, str) or _serialized_len(trimmed) <= max_chars:
                continue
            base = dict(trimmed)
            base[key] = ""
            limit = max(0, max_chars - _serialized_len(base) - 2)
            if len(inner) > limit:
                trimmed[key] = inner[:limit]
                omitted += 1
        for _key, inner in list(trimmed.items()):
            if not isinstance(inner, list):
                continue
            while inner and _serialized_len(trimmed) > max_chars:
                inner.pop()
                omitted += 1
                if _serialized_len(trimmed) <= max_chars:
                    break
        return trimmed, omitted
    return value, 0


def apply_section_quotas(
    ordered_names: Sequence[str],
    sections: Dict[str, Any],
    truncated: Dict[str, int],
) -> Dict[str, Any]:
    """Preenche cada seção até a cota; sobra volta ao pool das seguintes."""
    pool = 0
    filled: Dict[str, Any] = {}
    for name in ordered_names:
        quota = CONTEXT_BUDGET_BY_SECTION.get(name, 800) + pool
        value = sections.get(name, [])
        if _is_empty_section(value):
            filled[name] = [] if value is None else value
            pool = quota
            continue
        size = _serialized_len(value)
        if size <= quota:
            filled[name] = value
            pool = quota - size
            continue
        trimmed, omitted = _trim_value(value, quota)
        filled[name] = trimmed
        if omitted:
            truncated[name] = truncated.get(name, 0) + omitted
        pool = 0
    return filled


def _enforce_context_budget(payload: dict, max_chars: int) -> None:
    if _serialized_len(payload) <= max_chars:
        return

    def _mark(section: str, omitted: int = 1) -> None:
        truncated = dict(payload.get("truncated") or {})
        truncated[section] = truncated.get(section, 0) + omitted
        payload["truncated"] = truncated
        warnings = set(payload.get("warnings") or [])
        warnings.add("truncated_for_budget")
        payload["warnings"] = sorted(warnings)

    sections = payload.get("sections")
    if isinstance(sections, dict):
        for name in ("symbol", "layer", "brief_layer"):
            value = sections.get(name)
            if isinstance(value, dict) and value.pop("purpose", None) is not None:
                _mark(name)
            if isinstance(value, dict) and value.pop("summary", None) is not None:
                _mark(name)
        if sections.pop("file_summary", None) is not None:
            _mark("file_summary")
        if _serialized_len(payload) <= max_chars:
            return
        for name in list(sections):
            value = sections[name]
            while not _is_empty_section(value) and _serialized_len(payload) > max_chars:
                if isinstance(value, list) and value:
                    value.pop()
                    _mark(name)
                elif isinstance(value, dict):
                    trimmed, omitted = _trim_value(value, max(0, max_chars // 4))
                    sections[name] = trimmed
                    value = trimmed
                    _mark(name, max(omitted, 1))
                    break
                else:
                    sections[name] = []
                    _mark(name)
                    break
                if _serialized_len(payload) <= max_chars:
                    return


def _has_calls_edges(graph: dict) -> bool:
    return any(edge.get("kind") == "calls" for edge in graph.get("edges", []))


def _seed_ids(graph: dict, node: dict) -> List[str]:
    seeds = [node["id"]]
    file_path = node.get("file_path")
    if file_path:
        file_id = f"file:{file_path}"
        if file_id in graph.get("_nodes_by_id", {}) and file_id not in seeds:
            seeds.append(file_id)
    for edge in graph.get("edges", []):
        if edge["kind"] == "contains" and edge["source"] == node["id"] and edge["target"] not in seeds:
            seeds.append(edge["target"])
    return seeds


def _relation_hits(
    graph: dict, node: dict, *, incoming: bool
) -> List[dict]:
    nodes_by_id = graph.get("_nodes_by_id") or {}
    seeds = set(_seed_ids(graph, node))
    hits: List[dict] = []
    seen = set()

    if incoming:
        reverse = graph.get("_reverse_adjacency") or {}
        for sid in seeds:
            for neighbor_id, kind, edge in reverse.get(sid, []):
                if kind not in _RELATION_KINDS or neighbor_id in seeds or neighbor_id in seen:
                    continue
                neighbor = nodes_by_id.get(neighbor_id)
                if neighbor is None or (is_noise_hub(neighbor) and neighbor_id != node["id"]):
                    continue
                seen.add(neighbor_id)
                item = _node_summary(neighbor)
                item["via"] = kind
                location = _via_location(neighbor, edge)
                if location:
                    item["via_location"] = location
                hits.append(item)
        return hits

    for edge in graph.get("edges", []):
        if edge["source"] not in seeds or edge["kind"] not in _RELATION_KINDS:
            continue
        target_id = edge["target"]
        if target_id in seeds or target_id in seen:
            continue
        target = nodes_by_id.get(target_id)
        if target is None or is_noise_hub(target):
            continue
        seen.add(target_id)
        item = _node_summary(target)
        item["via"] = edge["kind"]
        location = _via_location(node, edge)
        if location:
            item["via_location"] = location
        hits.append(item)
    return hits


def _is_test_basename(name: str) -> bool:
    lower = name.casefold()
    return (
        (lower.startswith("test_") and lower.endswith(".py"))
        or lower.endswith("_test.py")
        or lower.endswith("_test.go")
        or lower.endswith("test.java")
        or lower.endswith("tests.java")
    )


def discover_tests(graph: dict, node: dict, manifest, affected_items: Iterable[dict]) -> Tuple[List[dict], List[str]]:
    # @MindWhy: só convenção + imports reversos — search_hybrid acordaria fastembed (DECISÃO-005)
    files = _manifest_files(manifest)
    related_paths: List[str] = []
    if node.get("file_path"):
        related_paths.append(node["file_path"])
    for item in affected_items:
        file_path = item.get("file_path") if isinstance(item, dict) else None
        if file_path:
            related_paths.append(file_path)

    convention_names = set()
    for file_path in related_paths:
        stem = posixpath.splitext(posixpath.basename(file_path))[0]
        if stem:
            convention_names.add(f"test_{stem}.py")
            convention_names.add(f"{stem}_test.go")
            convention_names.add(f"{stem}Test.java")
    label = str(node.get("label") or "")
    if label:
        convention_names.add(f"{label}Test.java")

    hits: Dict[str, dict] = {}
    for file_path in files:
        if posixpath.basename(file_path) in convention_names:
            hits[file_path] = {
                "file_path": file_path,
                "confidence": "inferred",
                "via": "convention",
            }

    import_hit = False
    target_file = node.get("file_path")
    if target_file:
        file_id = f"file:{target_file}"
        reverse = graph.get("_reverse_adjacency") or {}
        nodes_by_id = graph.get("_nodes_by_id") or {}
        for neighbor_id, kind, _edge in reverse.get(file_id, []):
            if kind != "imports":
                continue
            source = nodes_by_id.get(neighbor_id)
            source_path = source.get("file_path") if source else None
            if not source_path:
                continue
            base = posixpath.basename(source_path)
            if base not in convention_names and not _is_test_basename(base):
                continue
            import_hit = True
            hits[source_path] = {
                "file_path": source_path,
                "confidence": "inferred",
                "via": "imports",
            }

    extra_warnings: List[str] = []
    if not import_hit:
        extra_warnings.append("test_discovery_convention_only")
    return list(hits.values()), extra_warnings


def _layer_for_file(brief: Optional[dict], file_path: Optional[str]) -> dict:
    if not brief or not file_path:
        return {}
    best = None
    best_len = -1
    for layer in brief.get("layers") or []:
        path = layer.get("path") or ""
        if path in {"", "(root)"}:
            if best is None:
                best = layer
            continue
        prefix = path if file_path == path or file_path.startswith(path + "/") else None
        if prefix is not None and len(path) > best_len:
            best = layer
            best_len = len(path)
    if best is None:
        return {}
    return {
        "path": best.get("path"),
        "role": best.get("role"),
        "files": best.get("files"),
        "rank_basis": best.get("rank_basis"),
    }


def _summary_for_file(sidecar: Optional[dict], file_path: Optional[str]) -> Optional[str]:
    if not sidecar or not file_path:
        return None
    item = (sidecar.get("file_summaries") or {}).get(file_path)
    return item.get("summary") if isinstance(item, dict) and item.get("summary") else None


def _summary_for_layer(sidecar: Optional[dict], layer_path: Optional[str]) -> Optional[str]:
    if not sidecar or not layer_path:
        return None
    item = (sidecar.get("layer_summaries") or {}).get(layer_path)
    return item.get("summary") if isinstance(item, dict) and item.get("summary") else None


def _call_chain_to_entrypoints(graph: dict, node: dict, brief: Optional[dict]) -> List[dict]:
    entry_paths = set()
    if brief:
        for item in brief.get("entrypoints") or []:
            if isinstance(item, dict) and item.get("file_path"):
                entry_paths.add(item["file_path"])
            elif isinstance(item, str):
                entry_paths.add(item)
    if not entry_paths:
        return []

    start_id = f"file:{node['file_path']}" if node.get("file_path") else node["id"]
    nodes_by_id = graph.get("_nodes_by_id") or {}
    reverse = graph.get("_reverse_adjacency") or {}
    if start_id not in nodes_by_id:
        return []

    start_node = nodes_by_id[start_id]
    if start_node.get("file_path") in entry_paths:
        return [_node_summary(start_node)]

    queue: deque[tuple[str, list]] = deque([(start_id, [])])
    visited = {start_id}
    while queue:
        current_id, trail = queue.popleft()
        for neighbor_id, kind, _edge in reverse.get(current_id, []):
            if kind != "imports" or neighbor_id in visited:
                continue
            neighbor = nodes_by_id.get(neighbor_id)
            if neighbor is None or is_noise_hub(neighbor):
                continue
            visited.add(neighbor_id)
            next_trail = trail + [neighbor]
            if neighbor.get("file_path") in entry_paths:
                return [_node_summary(step) for step in reversed(next_trail)] + [
                    _node_summary(start_node)
                ]
            queue.append((neighbor_id, next_trail))
    return []


def _adrs(graph: dict, node: dict) -> List[dict]:
    neighborhood = explain(graph, node["id"])
    items = []
    seen = set()
    for note in neighborhood.get("notes") or []:
        note_id = note.get("id")
        if note_id in seen:
            continue
        seen.add(note_id)
        items.append(note)
    for rat in neighborhood.get("rationale") or []:
        rat_id = rat.get("id")
        if rat_id in seen:
            continue
        seen.add(rat_id)
        items.append(
            {
                "id": rat.get("id"),
                "label": rat.get("label"),
                "file_path": rat.get("file_path"),
                "lines": rat.get("lines"),
                "kind": "rationale",
            }
        )
    return items


def build_context(
    graph: dict,
    *,
    target: str,
    intent: str,
    manifest,
    brief: Optional[dict] = None,
    semantic_enabled: bool = False,
    semantic_ready: bool = False,
    semantic_sidecar: Optional[dict] = None,
    purpose_lookup=None,
) -> dict:
    intent = (intent or "").strip()
    if intent not in VALID_INTENTS:
        raise ValueError(
            "O parâmetro 'intent' deve ser 'edit', 'debug', 'review' ou 'understand'."
        )

    node = resolve_node(graph, target)
    warnings: List[str] = []
    truncated: Dict[str, int] = {}
    section_names = INTENT_SECTIONS[intent]

    if semantic_enabled and not semantic_ready:
        warnings.append("semantic_layer_unavailable")

    if not _has_calls_edges(graph) and intent in {"edit", "debug", "review"}:
        warnings.append("calls_unavailable")

    impact = affected(graph, node["id"])
    if "calls_unavailable" in (impact.get("warnings") or []) and "calls_unavailable" not in warnings:
        warnings.append("calls_unavailable")

    tests, test_warnings = discover_tests(graph, node, manifest, impact.get("items") or [])
    warnings.extend(test_warnings)

    neighborhood = explain(graph, node["id"])
    sections: Dict[str, Any] = {}

    if intent == "edit":
        sections = {
            "symbol": _node_summary(node),
            "callers": _relation_hits(graph, node, incoming=True),
            "callees": _relation_hits(graph, node, incoming=False),
            "tests": tests,
            "rationale": neighborhood.get("rationale") or [],
        }
    elif intent == "debug":
        warnings.append("git_history_unavailable")
        warnings.append("error_path_unavailable")
        sections = {
            "symbol": _node_summary(node),
            "call_chain_to_entrypoints": _call_chain_to_entrypoints(graph, node, brief),
            "error_handling": [],
            "recent_history": [],
        }
    elif intent == "review":
        warnings.append("diff_unavailable")
        sections = {
            "diff": [],
            "impact": impact.get("items") or [],
            "tests": tests,
            "adrs": _adrs(graph, node),
        }
    else:
        layer = _layer_for_file(brief, node.get("file_path"))
        if semantic_enabled and semantic_ready:
            layer_summary = _summary_for_layer(semantic_sidecar, layer.get("path"))
            if layer_summary:
                layer["summary"] = layer_summary
        symbol = _node_summary(node)
        if semantic_enabled and semantic_ready and callable(purpose_lookup):
            purpose = purpose_lookup(node.get("file_path", ""), node.get("label", ""))
            if purpose:
                symbol["purpose"] = purpose
        sections = {
            "symbol": symbol,
            "layer": layer,
            "neighbors": neighborhood.get("neighbors") or {},
            "brief_layer": dict(layer),
        }
        if semantic_enabled and semantic_ready:
            file_summary = _summary_for_file(semantic_sidecar, node.get("file_path"))
            if file_summary:
                sections["file_summary"] = file_summary
                section_names = ("symbol", "file_summary", "layer", "neighbors", "brief_layer")

    sections = apply_section_quotas(section_names, sections, truncated)
    payload = {
        "target": _node_summary(node),
        "intent": intent,
        "sections": sections,
        "warnings": sorted(set(warnings)),
        "budget": {"max_chars": CONTEXT_RESPONSE_MAX_CHARS, "used_chars": 0},
    }
    if truncated:
        payload["truncated"] = truncated
    _enforce_context_budget(payload, CONTEXT_RESPONSE_MAX_CHARS)
    payload["budget"]["used_chars"] = _serialized_len(payload)
    if not payload.get("truncated"):
        payload.pop("truncated", None)
    return payload
