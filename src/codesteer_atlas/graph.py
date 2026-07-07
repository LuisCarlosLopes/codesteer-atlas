import hashlib
import json
import os
import posixpath
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from codesteer_atlas.config import GRAPH_FILENAME, GRAPH_PATH_MAX_HOPS, GRAPH_TOP_HUBS_LIMIT
from codesteer_atlas.markdown_links import extract_markdown_link_targets
from codesteer_atlas.models import CodeChunk
from codesteer_atlas.rationale import decode_references_json, deserialize_rationale_ref
from codesteer_atlas.viewer import write_graph_html

_GRAPH_CACHE_LOCK = threading.Lock()
_GRAPH_CACHE: Dict[str, Any] = {
    "path": None,
    "mtime_ns": None,
    "size": None,
    "graph": None,
}


def _stem_to_paths(manifest) -> Dict[str, List[str]]:
    name_to_paths: Dict[str, List[str]] = {}
    for file_path in manifest.files:
        if not file_path.lower().endswith(".md"):
            continue
        stem = posixpath.basename(file_path)[: -len(".md")].lower()
        name_to_paths.setdefault(stem, []).append(file_path)
    return name_to_paths


def _resolve_note_matches(name_to_paths: Dict[str, List[str]], key: str) -> List[str]:
    if key in name_to_paths:
        return sorted(name_to_paths[key])
    prefix_matches: List[str] = []
    prefix = f"{key}-"
    for stem, paths in name_to_paths.items():
        if stem.startswith(prefix):
            prefix_matches.extend(paths)
    return sorted(prefix_matches)


def _resolve_python_import(source_path: str, raw_import: str, manifest_files: set[str]) -> Optional[str]:
    leading_dots = len(raw_import) - len(raw_import.lstrip("."))
    module_part = raw_import.lstrip(".")
    module_parts = [part for part in module_part.split(".") if part]

    candidates: List[str] = []
    if leading_dots:
        source_dir = posixpath.dirname(source_path)
        base = source_dir
        for _ in range(max(leading_dots - 1, 0)):
            base = posixpath.dirname(base)
        suffix = "/".join(module_parts) if module_parts else ""
        resolved_base = posixpath.normpath(posixpath.join(base, suffix)) if suffix else base
        candidates.extend(
            [
                f"{resolved_base}.py",
                posixpath.join(resolved_base, "__init__.py"),
            ]
        )
    else:
        parts = module_parts
        while parts:
            joined = "/".join(parts)
            candidates.append(f"{joined}.py")
            candidates.append(posixpath.join(joined, "__init__.py"))
            parts = parts[:-1]

    for candidate in candidates:
        normalized = posixpath.normpath(candidate)
        if normalized in manifest_files:
            return normalized
    return None


def _resolve_js_ts_import(source_path: str, raw_import: str, manifest_files: set[str]) -> Optional[str]:
    if not raw_import.startswith(("./", "../")):
        return None
    base = posixpath.dirname(source_path)
    target = posixpath.normpath(posixpath.join(base, raw_import))
    candidates = [
        f"{target}.ts",
        f"{target}.tsx",
        f"{target}.js",
        f"{target}.jsx",
        posixpath.join(target, "index.ts"),
        posixpath.join(target, "index.tsx"),
        posixpath.join(target, "index.js"),
        posixpath.join(target, "index.jsx"),
    ]
    for candidate in candidates:
        if candidate in manifest_files:
            return candidate
    return None


def _node_summary(node: dict) -> dict:
    return {
        "id": node["id"],
        "label": node.get("label"),
        "kind": node.get("kind"),
        "file_path": node.get("file_path"),
        "lines": node.get("lines"),
        "degree": node.get("degree", 0),
    }


def _build_adjacency(graph: dict) -> Dict[str, List[Tuple[str, str]]]:
    adjacency: Dict[str, List[Tuple[str, str]]] = {}
    for edge in graph.get("edges", []):
        adjacency.setdefault(edge["source"], []).append((edge["target"], edge["kind"]))
        adjacency.setdefault(edge["target"], []).append((edge["source"], edge["kind"]))
    return adjacency


def _clear_graph_cache(graph_path: Optional[Path] = None) -> None:
    with _GRAPH_CACHE_LOCK:
        cached_path = _GRAPH_CACHE.get("path")
        if graph_path is not None and cached_path is not None and Path(cached_path) != Path(graph_path):
            return
        _GRAPH_CACHE.update({"path": None, "mtime_ns": None, "size": None, "graph": None})


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def _graph_metadata(graph: dict, index_path: Path) -> dict:
    graph_path = Path(index_path) / GRAPH_FILENAME
    html_path = Path(index_path) / "graph.html"
    return {
        "graph_nodes": len(graph.get("nodes", [])),
        "graph_edges": len(graph.get("edges", [])),
        "graph_bytes": graph_path.stat().st_size if graph_path.exists() else 0,
        "graph_html_bytes": html_path.stat().st_size if html_path.exists() else 0,
    }


def _persist_graph(graph: dict, index_path: Path) -> tuple[Path, dict]:
    index_path = Path(index_path)
    index_path.mkdir(parents=True, exist_ok=True)
    graph_path = index_path / GRAPH_FILENAME
    _write_json_atomic(graph_path, graph)
    _clear_graph_cache(graph_path)

    try:
        write_graph_html(graph, index_path)
    except Exception as e:
        print(f"[atlas] Falha ao gerar graph.html: {e}", file=sys.stderr)

    return graph_path, _graph_metadata(graph, index_path)


def _build_empty_graph(manifest) -> dict:
    return {
        "graph_version": "1.0",
        "generated_at": manifest.last_indexed_at,
        "workspace_repo": manifest.repos_indexed[0] if manifest.repos_indexed else "",
        "import_languages": ["python", "javascript", "typescript"],
        "nodes": [],
        "edges": [],
        "metrics": {
            "node_count": 0,
            "edge_count": 0,
            "top_hubs": [],
        },
    }


def _add_contribution_from_rows(
    file_path: str,
    rows: Iterable[dict],
    raw_imports: List[str],
    manifest_files: set[str],
    name_to_paths: Dict[str, List[str]],
    nodes: Dict[str, dict],
    edges: List[dict],
    edge_keys: set[tuple[str, str, str]],
) -> None:
    file_kind = "doc" if file_path.lower().endswith(".md") else "file"
    file_node_id = f"file:{file_path}"
    if file_node_id not in nodes:
        nodes[file_node_id] = {
            "id": file_node_id,
            "kind": file_kind,
            "label": posixpath.basename(file_path),
            "file_path": file_path,
            "lines": None,
        }

    def _add_edge(source: str, target: str, kind: str) -> None:
        key = (source, target, kind)
        if source == target or key in edge_keys:
            return
        edge_keys.add(key)
        edges.append({"source": source, "target": target, "kind": kind})

    for row in rows:
        references = decode_references_json(row.get("references_json"))
        is_markdown = row["language"] == "markdown"
        if is_markdown:
            node_id = f"sec:{file_path}#{row['scope_name']}"
            node_kind = "section"
        else:
            node_id = f"sym:{file_path}#{row['scope_name']}"
            node_kind = "symbol"
        lines = [row["start_line"], row["end_line"]]
        nodes[node_id] = {
            "id": node_id,
            "kind": node_kind,
            "label": row["scope_name"],
            "file_path": file_path,
            "lines": lines,
        }
        _add_edge(file_node_id, node_id, "contains")

        if is_markdown:
            for target in extract_markdown_link_targets(
                row.get("content") or "", file_path, name_to_paths=name_to_paths
            ):
                if target.file_path is None:
                    continue
                target_id = f"file:{target.file_path}"
                if target_id not in nodes:
                    continue
                _add_edge(node_id, target_id, "links_to")
            continue

        for raw_ref in references:
            ref = deserialize_rationale_ref(raw_ref)
            if ref is None:
                continue
            if ref.kind == "annotation":
                signature = f"{ref.key}:{ref.text or ''}"
                rat_id = f"rat:{hashlib.sha1(signature.encode('utf-8')).hexdigest()[:12]}"
                nodes[rat_id] = {
                    "id": rat_id,
                    "kind": "rationale",
                    "label": ref.text or "",
                    "file_path": file_path,
                    "lines": lines,
                }
                _add_edge(node_id, rat_id, "annotates")
                continue
            if ref.kind not in {"cite", "wikilink"}:
                continue
            matches = _resolve_note_matches(name_to_paths, ref.key)
            if len(matches) != 1:
                continue
            target_id = f"file:{matches[0]}"
            if target_id in nodes:
                _add_edge(node_id, target_id, "cites")

    source_id = f"file:{file_path}"
    for raw_import in raw_imports:
        target_path = None
        if file_path.endswith(".py"):
            target_path = _resolve_python_import(file_path, raw_import, manifest_files)
        elif file_path.endswith((".js", ".jsx", ".ts", ".tsx")):
            target_path = _resolve_js_ts_import(file_path, raw_import, manifest_files)
        if target_path is None:
            continue
        target_id = f"file:{target_path}"
        if target_id in nodes:
            _add_edge(source_id, target_id, "imports")


def _finalize_graph(graph: dict) -> dict:
    nodes = {node["id"]: dict(node) for node in graph.get("nodes", [])}
    edges = list(graph.get("edges", []))

    degree_by_id = {node_id: 0 for node_id in nodes}
    for edge in edges:
        if edge["kind"] == "contains":
            continue
        degree_by_id[edge["source"]] = degree_by_id.get(edge["source"], 0) + 1
        degree_by_id[edge["target"]] = degree_by_id.get(edge["target"], 0) + 1
    for node_id, degree in degree_by_id.items():
        nodes[node_id]["degree"] = degree

    top_hubs = sorted(
        ({"id": node_id, "degree": degree} for node_id, degree in degree_by_id.items()),
        key=lambda item: (-item["degree"], item["id"]),
    )[:GRAPH_TOP_HUBS_LIMIT]

    graph["nodes"] = sorted(nodes.values(), key=lambda node: node["id"])
    graph["edges"] = sorted(edges, key=lambda edge: (edge["source"], edge["target"], edge["kind"]))
    graph["metrics"] = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "top_hubs": top_hubs,
    }
    return graph


def _graph_rows_from_chunks(chunks: List[CodeChunk]) -> Dict[str, List[dict]]:
    rows_by_file: Dict[str, List[dict]] = {}
    for chunk in chunks:
        rows_by_file.setdefault(chunk.file_path, []).append(
            {
                "file_path": chunk.file_path,
                "scope_type": chunk.scope_type,
                "scope_name": chunk.scope_name,
                "language": chunk.language,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content": chunk.content if chunk.language == "markdown" else None,
                "references_json": json.dumps(chunk.references or [], ensure_ascii=False),
            }
        )
    return rows_by_file


def build_and_write(storage, manifest, index_path: Path, return_metadata: bool = False):
    """
    Reconstrói `graph.json` inteiro a partir do estado atual do índice.
    """
    rows = storage.get_graph_projection()
    manifest_files = set(manifest.files.keys())
    name_to_paths = _stem_to_paths(manifest)
    rows_by_file: Dict[str, List[dict]] = {}
    for row in rows:
        rows_by_file.setdefault(row["file_path"], []).append(row)

    nodes: Dict[str, dict] = {
        f"file:{file_path}": {
            "id": f"file:{file_path}",
            "kind": "doc" if file_path.lower().endswith(".md") else "file",
            "label": posixpath.basename(file_path),
            "file_path": file_path,
            "lines": None,
        }
        for file_path in sorted(manifest_files)
    }
    edges: List[dict] = []
    edge_keys: set[tuple[str, str, str]] = set()
    for file_path in sorted(manifest_files):
        _add_contribution_from_rows(
            file_path=file_path,
            rows=rows_by_file.get(file_path, []),
            raw_imports=manifest.files_imports.get(file_path, []),
            manifest_files=manifest_files,
            name_to_paths=name_to_paths,
            nodes=nodes,
            edges=edges,
            edge_keys=edge_keys,
        )

    graph = _finalize_graph(_build_empty_graph(manifest) | {"nodes": list(nodes.values()), "edges": edges})
    graph_path, metadata = _persist_graph(graph, index_path)
    if return_metadata:
        return graph_path, metadata
    return graph_path


def build_and_write_incremental(
    index_path: Path,
    manifest,
    updated_chunks: List[CodeChunk],
    updated_file_paths: set[str],
) -> tuple[Path, dict]:
    """
    Atualiza `graph.json` a partir do grafo anterior quando apenas arquivos de
    código já existentes mudaram, evitando rebuild completo do índice.
    """
    graph = load_graph(index_path)
    nodes = {node["id"]: dict(node) for node in graph.get("nodes", [])}
    removed_node_ids = {
        node_id
        for node_id, node in nodes.items()
        if node.get("file_path") in updated_file_paths and node.get("kind") in {"symbol", "section", "rationale"}
    }
    for node_id in removed_node_ids:
        nodes.pop(node_id, None)

    kept_edges: List[dict] = []
    for edge in graph.get("edges", []):
        if edge["source"] in removed_node_ids or edge["target"] in removed_node_ids:
            continue
        if edge["kind"] in {"contains", "imports"} and edge["source"].startswith("file:"):
            source_file = edge["source"][len("file:") :]
            if source_file in updated_file_paths:
                continue
        kept_edges.append(edge)

    edge_keys = {(edge["source"], edge["target"], edge["kind"]) for edge in kept_edges}
    manifest_files = set(manifest.files.keys())
    name_to_paths = _stem_to_paths(manifest)
    rows_by_file = _graph_rows_from_chunks(updated_chunks)

    for file_path in updated_file_paths:
        _add_contribution_from_rows(
            file_path=file_path,
            rows=rows_by_file.get(file_path, []),
            raw_imports=manifest.files_imports.get(file_path, []),
            manifest_files=manifest_files,
            name_to_paths=name_to_paths,
            nodes=nodes,
            edges=kept_edges,
            edge_keys=edge_keys,
        )

    updated_graph = _build_empty_graph(manifest) | {
        "nodes": list(nodes.values()),
        "edges": kept_edges,
    }
    updated_graph = _finalize_graph(updated_graph)
    return _persist_graph(updated_graph, index_path)


def load_graph(index_dir: Path) -> dict:
    graph_path = Path(index_dir) / GRAPH_FILENAME
    if not graph_path.exists():
        raise FileNotFoundError(
            "graph.json não encontrado. Execute atlas_index para gerar o grafo "
            "(índices anteriores a 2.1.0 não possuem grafo)."
        )
    stat = graph_path.stat()
    with _GRAPH_CACHE_LOCK:
        if (
            _GRAPH_CACHE.get("path") == str(graph_path.resolve())
            and _GRAPH_CACHE.get("mtime_ns") == stat.st_mtime_ns
            and _GRAPH_CACHE.get("size") == stat.st_size
            and _GRAPH_CACHE.get("graph") is not None
        ):
            return _GRAPH_CACHE["graph"]

    with open(graph_path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    graph["_nodes_by_id"] = {node["id"]: node for node in graph.get("nodes", [])}
    graph["_adjacency"] = _build_adjacency(graph)
    with _GRAPH_CACHE_LOCK:
        _GRAPH_CACHE.update(
            {
                "path": str(graph_path.resolve()),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "graph": graph,
            }
        )
    return graph


def resolve_node(graph: dict, ref: str) -> dict:
    ref = ref.strip()
    if not ref:
        raise ValueError("O parâmetro de nó não pode ser vazio.")

    nodes_by_id = graph["_nodes_by_id"]
    if ref in nodes_by_id:
        return nodes_by_id[ref]

    lowered = ref.casefold()
    exact_labels = [node for node in nodes_by_id.values() if str(node.get("label", "")).casefold() == lowered]
    if len(exact_labels) == 1:
        return exact_labels[0]
    if len(exact_labels) > 1:
        raise ValueError(
            "Referência ambígua; candidatos: "
            + ", ".join(sorted(node["id"] for node in exact_labels))
        )

    exact_paths = [
        node
        for node in nodes_by_id.values()
        if str(node.get("file_path") or "").casefold() == lowered
    ]
    preferred_exact_paths = [node for node in exact_paths if node.get("kind") in {"file", "doc"}]
    if len(preferred_exact_paths) == 1:
        return preferred_exact_paths[0]
    if len(exact_paths) == 1:
        return exact_paths[0]
    if len(preferred_exact_paths) > 1 or len(exact_paths) > 1:
        candidates = preferred_exact_paths or exact_paths
        raise ValueError(
            "Referência ambígua; candidatos: "
            + ", ".join(sorted(node["id"] for node in candidates))
        )

    suffix_matches = [
        node
        for node in nodes_by_id.values()
        if node["id"].casefold().endswith(lowered)
        or str(node.get("file_path") or "").casefold().endswith(lowered)
        or str(node.get("label") or "").casefold().endswith(lowered)
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    preferred_suffix_matches = [
        node
        for node in suffix_matches
        if node.get("kind") in {"file", "doc"}
        and str(node.get("file_path") or "").casefold().endswith(lowered)
    ]
    if len(preferred_suffix_matches) == 1:
        return preferred_suffix_matches[0]
    if len(suffix_matches) > 1:
        raise ValueError(
            "Referência ambígua; candidatos: "
            + ", ".join(sorted(node["id"] for node in suffix_matches))
        )
    raise ValueError(f"Nó '{ref}' não encontrado.")


def bfs_path(graph: dict, source_ref: str, target_ref: str, max_hops: int = GRAPH_PATH_MAX_HOPS) -> dict:
    source = resolve_node(graph, source_ref)
    target = resolve_node(graph, target_ref)
    if source["id"] == target["id"]:
        return {"found": True, "path": [{"node": _node_summary(source), "edge_kind_to_next": None}], "hops": 0}

    adjacency = graph["_adjacency"]
    queue = deque([(source["id"], [])])
    visited = {source["id"]}

    while queue:
        node_id, trail = queue.popleft()
        if len(trail) >= max_hops:
            continue
        for neighbor_id, edge_kind in adjacency.get(node_id, []):
            if neighbor_id in visited:
                continue
            next_trail = trail + [(node_id, edge_kind, neighbor_id)]
            if neighbor_id == target["id"]:
                sequence = []
                for current_id, current_edge_kind, next_id in next_trail:
                    sequence.append(
                        {
                            "node": _node_summary(graph["_nodes_by_id"][current_id]),
                            "edge_kind_to_next": current_edge_kind,
                        }
                    )
                sequence.append({"node": _node_summary(target), "edge_kind_to_next": None})
                return {"found": True, "path": sequence, "hops": len(next_trail)}
            visited.add(neighbor_id)
            queue.append((neighbor_id, next_trail))

    return {"found": False, "path": [], "hops": 0}


def hubs(graph: dict, top_n: int) -> List[dict]:
    result = []
    for item in graph.get("metrics", {}).get("top_hubs", [])[:top_n]:
        node = graph["_nodes_by_id"].get(item["id"])
        if node is None:
            continue
        result.append(
            {
                "id": node["id"],
                "label": node.get("label"),
                "kind": node.get("kind"),
                "degree": item["degree"],
                "file_path": node.get("file_path"),
            }
        )
    return result


def explain(graph: dict, ref: str) -> dict:
    node = resolve_node(graph, ref)
    adjacency = graph["_adjacency"]
    neighbors: Dict[str, List[dict]] = {}
    rationale_nodes: List[dict] = []
    notes: List[dict] = []

    for neighbor_id, edge_kind in adjacency.get(node["id"], []):
        neighbor = graph["_nodes_by_id"][neighbor_id]
        summary = _node_summary(neighbor)
        summary["edge_kind"] = edge_kind
        neighbors.setdefault(neighbor["kind"], []).append(summary)
        if neighbor["kind"] == "rationale":
            rationale_nodes.append(summary)
        if neighbor["kind"] == "doc":
            notes.append(
                {
                    "id": neighbor["id"],
                    "label": neighbor.get("label"),
                    "file_path": neighbor.get("file_path"),
                    "lines": neighbor.get("lines"),
                }
            )

    for kind in neighbors:
        neighbors[kind] = sorted(neighbors[kind], key=lambda item: (item["label"] or "", item["id"]))
    notes = sorted(notes, key=lambda item: item["file_path"] or "")

    return {
        "node": _node_summary(node),
        "neighbors": neighbors,
        "rationale": rationale_nodes,
        "notes": notes,
    }
