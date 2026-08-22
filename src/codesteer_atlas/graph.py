import hashlib
import json
import os
import posixpath
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from codesteer_atlas.config import (
    GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND,
    GRAPH_EXPLAIN_MAX_NOTES,
    GRAPH_FILENAME,
    GRAPH_PATH_MAX_HOPS,
    GRAPH_TOP_HUBS_LIMIT,
)
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


# Diretórios que convencionalmente hospedam pacotes sem serem parte do nome do módulo
_CONVENTIONAL_SOURCE_ROOTS = ("src", "lib")


def infer_package_roots(manifest_files: set[str]) -> List[str]:
    """
    Deduz os prefixos de diretório que NÃO fazem parte do nome do módulo.

    Um import absoluto como `codesteer_atlas.config` precisa casar com
    `src/codesteer_atlas/config.py`, mas as chaves do manifest são relativas ao
    workspace. Sem descobrir que `src/` é raiz de código, nenhum import absoluto
    resolve num layout `src/` — que é justamente o deste projeto.

    A dedução parte dos `__init__.py`: sobe enquanto o diretório-pai também for
    pacote, e a raiz é o pai do pacote mais externo. Retorna sempre a raiz do
    workspace ("") como fallback, e a lista é ordenada para tornar a resolução
    determinística.
    """
    package_dirs = {
        posixpath.dirname(file_path)
        for file_path in manifest_files
        if posixpath.basename(file_path) == "__init__.py"
    }

    roots = {""}
    for package_dir in package_dirs:
        outermost = package_dir
        while True:
            parent = posixpath.dirname(outermost)
            if parent == outermost or parent not in package_dirs:
                break
            outermost = parent
        roots.add(posixpath.dirname(outermost))

    # Layouts sem `__init__.py` (namespace packages, módulos soltos em src/)
    for candidate in _CONVENTIONAL_SOURCE_ROOTS:
        prefix = f"{candidate}/"
        if any(file_path.startswith(prefix) for file_path in manifest_files):
            roots.add(candidate)

    # Raiz do workspace primeiro; depois as mais rasas — determinístico
    return sorted(roots, key=lambda root: (len(root), root))


def resolve_module_path(
    module: str, manifest_files: set[str], package_roots: Optional[List[str]] = None
) -> Optional[str]:
    """
    Resolve um módulo pontuado absoluto (`pacote.modulo`) para um caminho do manifest,
    testando cada raiz de código deduzida. Vai encurtando o caminho à direita para
    também casar `from pacote.modulo import nome`, em que a última parte é um símbolo.
    """
    if package_roots is None:
        package_roots = infer_package_roots(manifest_files)

    parts = [part for part in module.split(".") if part]
    while parts:
        joined = "/".join(parts)
        for root in package_roots:
            base = posixpath.join(root, joined) if root else joined
            for candidate in (f"{base}.py", posixpath.join(base, "__init__.py")):
                normalized = posixpath.normpath(candidate)
                if normalized in manifest_files:
                    return normalized
        parts = parts[:-1]
    return None


def _resolve_python_import(
    source_path: str,
    raw_import: str,
    manifest_files: set[str],
    package_roots: Optional[List[str]] = None,
) -> Optional[str]:
    leading_dots = len(raw_import) - len(raw_import.lstrip("."))
    module_part = raw_import.lstrip(".")
    module_parts = [part for part in module_part.split(".") if part]

    if not leading_dots:
        return resolve_module_path(module_part, manifest_files, package_roots)

    # Import relativo: a âncora é o diretório do próprio arquivo, sem raízes de pacote
    source_dir = posixpath.dirname(source_path)
    base = source_dir
    for _ in range(max(leading_dots - 1, 0)):
        base = posixpath.dirname(base)
    suffix = "/".join(module_parts) if module_parts else ""
    resolved_base = posixpath.normpath(posixpath.join(base, suffix)) if suffix else base

    for candidate in (f"{resolved_base}.py", posixpath.join(resolved_base, "__init__.py")):
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


def _append_edge(
    edges: List[dict],
    edge_keys: set,
    source: str,
    target: str,
    kind: str,
    **attrs,
) -> None:
    """
    Acrescenta uma aresta respeitando dedup por `(source, target, kind)` e
    descartando auto-aresta. `attrs` carrega campos extras do kind — hoje só
    `resolution`, exclusivo de `calls`. [RE05 / R-CALL-10]
    """
    key = (source, target, kind)
    if source == target or key in edge_keys:
        return
    edge_keys.add(key)
    edge = {"source": source, "target": target, "kind": kind}
    edge.update(attrs)
    edges.append(edge)


def _short_symbol_name(scope_name: str) -> str:
    """
    Último segmento do `scope_name`: `StorageBackend.store_chunks` casa
    `store_chunks`. Comparação por igualdade exata e sensível a caixa —
    deliberadamente NÃO reusa `_resolve_note_matches`, que casa por prefixo e
    faria `get` casar `get_manifest`. [R-CALL-06 / A3]
    """
    return scope_name.rsplit(".", 1)[-1]


def _resolve_file_imports(
    file_path: str,
    raw_imports: Iterable[str],
    manifest_files: set[str],
    package_roots: Optional[List[str]],
) -> List[str]:
    """Paths do manifest alcançados pelos imports deste arquivo (degrau 2)."""
    targets: List[str] = []
    for raw_import in raw_imports:
        if file_path.endswith(".py"):
            target_path = _resolve_python_import(
                file_path, raw_import, manifest_files, package_roots
            )
        elif file_path.endswith((".js", ".jsx", ".ts", ".tsx")):
            # go/csharp/java não têm resolvedor de import no repositório: para eles
            # valem só os degraus 1 e 3. [R-CALL-09]
            target_path = _resolve_js_ts_import(file_path, raw_import, manifest_files)
        else:
            target_path = None
        if target_path is not None:
            targets.append(target_path)
    return targets


def _resolve_call_edges(
    nodes: Dict[str, dict],
    edges: List[dict],
    edge_keys: set,
    calls_by_symbol: Dict[str, List[str]],
    manifest,
    manifest_files: set[str],
    package_roots: Optional[List[str]],
) -> None:
    """
    Segunda passada: transforma nomes chamados em arestas `symbol -> symbol`.

    Roda depois de todos os arquivos terem contribuído com seus nós — resolver na
    primeira passada perderia toda chamada para arquivo ainda não processado
    (R-CALL-05) — e SEMPRE contra o conjunto de nós do grafo que está sendo
    escrito, nos dois caminhos de escrita. É isso que faz o incremental produzir
    o mesmo resultado que o rebuild completo. [R-CALL-12]

    Escada, avaliada em ordem para cada par (símbolo chamador, nome chamado):

      1. mesmo arquivo   -> `resolution: "exact"`
      2. via import      -> `resolution: "exact"`
      3. único no grafo  -> `resolution: "inferred"`
      4. descarte

    Zero candidatos cai para o degrau seguinte; exatamente um resolve e encerra;
    mais de um também cai — e como o universo do degrau 3 contém os anteriores,
    ambiguidade termina invariavelmente em descarte. Nunca se desempata por
    heurística: é a mesma política já adotada por `_resolve_note_matches`.
    """
    symbols_by_file: Dict[str, List[tuple[str, str]]] = {}
    by_short_name: Dict[str, List[str]] = {}
    for node_id, node in nodes.items():
        if node.get("kind") != "symbol":
            continue
        short = _short_symbol_name(node.get("label") or "")
        symbols_by_file.setdefault(node.get("file_path") or "", []).append((node_id, short))
        by_short_name.setdefault(short, []).append(node_id)

    imports_cache: Dict[str, List[str]] = {}

    for source_id in sorted(calls_by_symbol):
        names = calls_by_symbol[source_id]
        if not names or source_id not in nodes:
            continue
        caller_file = nodes[source_id].get("file_path") or ""

        if caller_file not in imports_cache:
            imports_cache[caller_file] = _resolve_file_imports(
                caller_file,
                manifest.files_imports.get(caller_file, []),
                manifest_files,
                package_roots,
            )
        imported_files = imports_cache[caller_file]

        for name in names:
            same_file = [nid for nid, short in symbols_by_file.get(caller_file, []) if short == name]
            if len(same_file) == 1:
                _append_edge(
                    edges, edge_keys, source_id, same_file[0], "calls", resolution="exact"
                )
                continue

            via_import = [
                nid
                for target_file in imported_files
                for nid, short in symbols_by_file.get(target_file, [])
                if short == name
            ]
            if len(via_import) == 1:
                _append_edge(
                    edges, edge_keys, source_id, via_import[0], "calls", resolution="exact"
                )
                continue

            unique_in_graph = by_short_name.get(name, [])
            if len(unique_in_graph) == 1:
                # `inferred` é pista, não fato: o nome era único no grafo, o que é
                # evidência fraca e some assim que um homônimo aparece. [R-CALL-08]
                _append_edge(
                    edges, edge_keys, source_id, unique_in_graph[0], "calls", resolution="inferred"
                )


def _add_contribution_from_rows(
    file_path: str,
    rows: Iterable[dict],
    raw_imports: List[str],
    manifest_files: set[str],
    name_to_paths: Dict[str, List[str]],
    nodes: Dict[str, dict],
    edges: List[dict],
    edge_keys: set[tuple[str, str, str]],
    package_roots: Optional[List[str]] = None,
    calls_by_symbol: Optional[Dict[str, List[str]]] = None,
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
        _append_edge(edges, edge_keys, source, target, kind)

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

        if calls_by_symbol is not None and not is_markdown:
            chunk_calls = decode_references_json(row.get("calls_json"))
            if chunk_calls:
                calls_by_symbol[node_id] = chunk_calls

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
            target_path = _resolve_python_import(
                file_path, raw_import, manifest_files, package_roots
            )
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
                # Paridade com `get_graph_projection`: e este o caminho vivo do
                # update incremental, e sem a chave aqui o incremental produziria
                # zero arestas `calls` enquanto o rebuild completo as produz. [A2]
                "calls_json": json.dumps(chunk.calls or [], ensure_ascii=False),
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
    package_roots = infer_package_roots(manifest_files)
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
    calls_by_symbol: Dict[str, List[str]] = {}
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
            package_roots=package_roots,
            calls_by_symbol=calls_by_symbol,
        )

    _resolve_call_edges(
        nodes, edges, edge_keys, calls_by_symbol, manifest, manifest_files, package_roots
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
    storage=None,
) -> tuple[Path, dict]:
    """
    Atualiza `graph.json` a partir do grafo anterior quando apenas arquivos de
    código já existentes mudaram, evitando rebuild completo do índice.

    Exceção deliberada: as arestas `calls`. Elas são descartadas por completo e
    re-resolvidas contra o índice inteiro a cada escrita, mesmo as de chamadores
    em arquivos não tocados. O motivo é que a escada depende do conjunto global —
    editar um arquivo e criar um homônimo de um nome antes único invalida uma
    aresta `inferred` cujo chamador ninguém encostou. Sem isso o incremental
    divergiria do rebuild em silêncio. O custo é uma leitura da projeção por
    execução, o mesmo preço já aceito por `brief.json`. [R-CALL-12 / RF17 / ADR-004]
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
        # Nenhuma aresta `calls` do grafo anterior é transportada. [RF17]
        if edge["kind"] == "calls":
            continue
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
    package_roots = infer_package_roots(manifest_files)
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
            package_roots=package_roots,
        )

    # Fonte da re-resolução é o ÍNDICE pós-persist, não `updated_chunks`: só ele
    # tem os chamadores dos arquivos não alterados, que também precisam ser
    # reavaliados. `get_graph_projection_for_file_paths` não serve aqui pela mesma
    # razão. Sem `storage` o grafo fica sem `calls` em vez de ficar com um
    # subconjunto silenciosamente errado. [A2 / R-CALL-12]
    if storage is not None:
        calls_by_symbol: Dict[str, List[str]] = {}
        for row in storage.get_graph_projection():
            if row.get("language") == "markdown":
                continue
            chunk_calls = decode_references_json(row.get("calls_json"))
            if chunk_calls:
                node_id = f"sym:{row['file_path']}#{row['scope_name']}"
                if node_id in nodes:
                    calls_by_symbol[node_id] = chunk_calls
        _resolve_call_edges(
            nodes, kept_edges, edge_keys, calls_by_symbol, manifest, manifest_files, package_roots
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
    queue: deque[tuple[str, list]] = deque([(source["id"], [])])
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
                for current_id, current_edge_kind, _next_id in next_trail:
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
    """
    Ordena TODOS os nós do grafo por `(-degree, id)` e devolve até `top_n`.

    Não fatia `metrics.top_hubs`: aquele campo é pré-computado com teto
    `GRAPH_TOP_HUBS_LIMIT` (25), enquanto a tool aceita `top_n` até 50 — fatiar
    devolvia 25 e chamava de 50. Para `top_n <= 25` o resultado é idêntico ao
    anterior, porque a ordenação é a mesma. [ADR-005]
    """
    ranked = sorted(
        graph.get("nodes", []),
        key=lambda node: (-(node.get("degree") or 0), node["id"]),
    )
    return [
        {
            "id": node["id"],
            "label": node.get("label"),
            "kind": node.get("kind"),
            "degree": node.get("degree") or 0,
            "file_path": node.get("file_path"),
        }
        for node in ranked[:top_n]
    ]


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

    # O corte vem DEPOIS da ordenação determinística, para a resposta ser estável
    # entre chamadas sobre o mesmo grafo. `omitted` registra o que ficou de fora —
    # sem ele, uma lista capada é lida como exaustiva. [ADR-005 / R-CAP-01..04]
    omitted: Dict[str, int] = {}

    for kind in neighbors:
        ordered = sorted(neighbors[kind], key=lambda item: (item["label"] or "", item["id"]))
        if len(ordered) > GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND:
            omitted[kind] = len(ordered) - GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND
            ordered = ordered[:GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND]
        neighbors[kind] = ordered

    notes = sorted(notes, key=lambda item: item["file_path"] or "")
    if len(notes) > GRAPH_EXPLAIN_MAX_NOTES:
        omitted["notes"] = len(notes) - GRAPH_EXPLAIN_MAX_NOTES
        notes = notes[:GRAPH_EXPLAIN_MAX_NOTES]

    # `rationale` é uma projeção de `neighbors["rationale"]`; corta no mesmo ponto
    # para as duas listas não se contradizerem na mesma resposta (R-CAP-03).
    rationale_nodes = neighbors.get("rationale", rationale_nodes)

    return {
        "node": _node_summary(node),
        "neighbors": neighbors,
        "rationale": rationale_nodes,
        "notes": notes,
        "omitted": omitted,
    }
