import hashlib
import json
import os
import posixpath
import re
import sys
import threading
from collections import deque
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from codesteer_atlas.config import (
    GRAPH_AFFECTED_MAX_RESULTS,
    GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND,
    GRAPH_FILENAME,
    GRAPH_NOISE_HUB_LABELS,
    GRAPH_PATH_MAX_HOPS,
    GRAPH_RESPONSE_MAX_CHARS,
    GRAPH_TOP_HUBS_LIMIT,
    IGNORE_DIRS,
    IMPORT_RESOLUTION_TIERS,
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

# Manifestos de build que marcam raiz de módulo. `go.mod` e `*.csproj` NÃO têm
# extensão em SUPPORTED_EXTENSIONS, logo não estão no manifest do índice — só são
# descobertos varrendo o workspace, e por isso `workspace_root` é opcional aqui.
_BUILD_MANIFEST_NAMES = frozenset({"go.mod", "pom.xml", "Cargo.toml", "build.gradle"})
_BUILD_MANIFEST_SUFFIXES = (".csproj",)
# Teto de profundidade: cobre monorepo (`services/api/go.mod`) sem varrer a árvore toda.
_BUILD_MANIFEST_MAX_DEPTH = 3

_GO_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)


def _is_build_manifest(name: str) -> bool:
    return name in _BUILD_MANIFEST_NAMES or name.endswith(_BUILD_MANIFEST_SUFFIXES)


def _discover_build_manifests(workspace_root: Optional[Path]) -> List[Tuple[str, str]]:
    """[(diretório relativo POSIX, nome do arquivo)] dos manifestos de build."""
    if workspace_root is None:
        return []
    root = Path(workspace_root)
    if not root.is_dir():
        return []

    found: List[Tuple[str, str]] = []
    for current_dir, dir_names, file_names in os.walk(root):
        rel_dir = PurePath(Path(current_dir).relative_to(root)).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        depth = 0 if not rel_dir else rel_dir.count("/") + 1
        if depth >= _BUILD_MANIFEST_MAX_DEPTH:
            dir_names[:] = []
        else:
            dir_names[:] = [
                name for name in dir_names if name not in IGNORE_DIRS and not name.startswith(".")
            ]
        for file_name in file_names:
            if _is_build_manifest(file_name):
                found.append((rel_dir, file_name))
    return sorted(found)


def infer_go_modules(
    manifest_files: set[str], workspace_root: Optional[Path] = None
) -> List[Tuple[str, str]]:
    """
    [(caminho do módulo declarado no `go.mod`, diretório relativo da raiz)].

    Ordenado do módulo mais longo para o mais curto para que, num monorepo com
    módulos aninhados, o prefixo mais específico case primeiro.
    """
    if workspace_root is None or not any(path.endswith(".go") for path in manifest_files):
        return []

    modules: List[Tuple[str, str]] = []
    for rel_dir, file_name in _discover_build_manifests(workspace_root):
        if file_name != "go.mod":
            continue
        try:
            content = (Path(workspace_root) / rel_dir / file_name).read_text(
                encoding="utf-8", errors="ignore"
            )
        except OSError:
            continue
        match = _GO_MODULE_RE.search(content)
        if match:
            modules.append((match.group(1).strip(), rel_dir))
    return sorted(modules, key=lambda item: (-len(item[0]), item[0]))


def infer_package_roots(
    manifest_files: set[str], workspace_root: Optional[Path] = None
) -> List[str]:
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

    Com `workspace_root`, o diretório de cada manifesto de build (`go.mod`,
    `pom.xml`, `*.csproj`, `Cargo.toml`) também vira raiz — sem ele, só os
    manifestos indexáveis (`pom.xml`, `Cargo.toml`) são vistos (§3.3).
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

    # Manifestos de build indexáveis já estão no manifest do índice
    for file_path in manifest_files:
        if _is_build_manifest(posixpath.basename(file_path)):
            roots.add(posixpath.dirname(file_path))

    for rel_dir, file_name in _discover_build_manifests(workspace_root):
        if _is_build_manifest(file_name):
            roots.add(rel_dir)
            # Layout Maven/Gradle: as fontes ficam sob src/main/<linguagem>
            for nested in ("src/main/java", "src/main/kotlin", "src/main/scala", "src"):
                candidate = posixpath.join(rel_dir, nested) if rel_dir else nested
                if any(path.startswith(f"{candidate}/") for path in manifest_files):
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


# Só estas duas arestas têm mais de um tier de qualidade possível; a presença do
# campo `origin` já significa "esta aresta tem tier" (DECISÃO-006).
_ORIGIN_EDGE_KINDS = frozenset({"imports", "calls"})
IMPORT_ORIGIN_TREESITTER = "treesitter"
# Grafo 2.1.0 não tem `origin`; o consumidor recebe isto em vez de nada (Princípio VI)
EDGE_ORIGIN_UNKNOWN = "unknown"


class _ImportContext:
    """Índices derivados uma vez por build e compartilhados por todos os resolvers."""

    __slots__ = (
        "manifest_files",
        "package_roots",
        "declares_index",
        "go_modules",
        "files_by_dir",
        "dirs_by_name",
    )

    def __init__(
        self,
        manifest_files: set[str],
        package_roots: List[str],
        files_declares: Dict[str, str],
        go_modules: List[Tuple[str, str]],
    ):
        self.manifest_files = manifest_files
        self.package_roots = package_roots
        self.go_modules = go_modules

        declares_index: Dict[str, List[str]] = {}
        for path, namespace in files_declares.items():
            declares_index.setdefault(namespace, []).append(path)
        self.declares_index = {key: sorted(value) for key, value in declares_index.items()}

        files_by_dir: Dict[str, List[str]] = {}
        for path in manifest_files:
            files_by_dir.setdefault(posixpath.dirname(path), []).append(path)
        self.files_by_dir = {key: sorted(value) for key, value in files_by_dir.items()}

        dirs_by_name: Dict[str, List[str]] = {}
        for directory in self.files_by_dir:
            if directory:
                dirs_by_name.setdefault(posixpath.basename(directory), []).append(directory)
        self.dirs_by_name = {key: sorted(value) for key, value in dirs_by_name.items()}


def _resolve_python_targets(source_path: str, raw_import: str, ctx: _ImportContext) -> List[str]:
    target = _resolve_python_import(
        source_path, raw_import, ctx.manifest_files, ctx.package_roots
    )
    return [target] if target else []


def _resolve_js_ts_targets(source_path: str, raw_import: str, ctx: _ImportContext) -> List[str]:
    target = _resolve_js_ts_import(source_path, raw_import, ctx.manifest_files)
    return [target] if target else []


def _files_in_package_dir(ctx: _ImportContext, directory: str, suffix: str, source_path: str):
    directory = "" if directory in {".", "/"} else directory.strip("/")
    return [
        path
        for path in ctx.files_by_dir.get(directory, ())
        if path.endswith(suffix) and path != source_path
    ]


def _resolve_go_import(source_path: str, raw_import: str, ctx: _ImportContext) -> List[str]:
    """
    Em Go o pacote é o diretório: o import aponta para TODOS os `.go` daquele diretório.

    O caminho do import é absoluto pelo `module` do `go.mod`; sem casar um módulo
    conhecido, o alvo é externo (stdlib ou dependência) e não gera aresta.
    """
    if raw_import.startswith(("./", "../")):
        target_dir = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), raw_import))
        return _files_in_package_dir(ctx, target_dir, ".go", source_path)

    for module, root in ctx.go_modules:
        if raw_import == module:
            remainder = ""
        elif raw_import.startswith(f"{module}/"):
            remainder = raw_import[len(module) + 1 :]
        else:
            continue
        target_dir = posixpath.join(root, remainder) if root else remainder
        return _files_in_package_dir(ctx, target_dir, ".go", source_path)
    return []


def _resolve_declared_namespace_import(
    source_path: str, raw_import: str, ctx: _ImportContext
) -> List[str]:
    """
    Java, C#, Kotlin e Scala: o namespace é declarado, não é o caminho (DECISÃO-003).

    Casa o prefixo mais longo do import que alguém declara. Se sobrar um segmento
    depois do namespace (`com.pkg.Service` sobre o pacote `com.pkg`), prefere o
    arquivo cujo nome é esse segmento; senão devolve todos os arquivos do namespace.
    """
    parts = [part for part in raw_import.split(".") if part]
    for cut in range(len(parts), 0, -1):
        paths = ctx.declares_index.get(".".join(parts[:cut]))
        if not paths:
            continue
        remainder = parts[cut:]
        if remainder:
            stem = remainder[0]
            exact = [
                path
                for path in paths
                if posixpath.splitext(posixpath.basename(path))[0] == stem
                and path != source_path
            ]
            if exact:
                return exact
        return [path for path in paths if path != source_path]
    return []


def _resolve_rust_import(source_path: str, raw_import: str, ctx: _ImportContext) -> List[str]:
    parts = [part for part in raw_import.split("::") if part]
    if not parts:
        return []
    source_dir = posixpath.dirname(source_path)
    head = parts[0]
    if head == "crate":
        rest, bases = parts[1:], list(ctx.package_roots)
    elif head == "self":
        rest, bases = parts[1:], [source_dir]
    elif head == "super":
        rest, bases = parts[1:], [posixpath.dirname(source_dir)]
    else:
        rest, bases = parts, [source_dir, *ctx.package_roots]

    while rest:
        joined = "/".join(rest)
        for base in bases:
            prefix = posixpath.join(base, joined) if base else joined
            for candidate in (f"{prefix}.rs", posixpath.join(prefix, "mod.rs")):
                normalized = posixpath.normpath(candidate)
                if normalized in ctx.manifest_files and normalized != source_path:
                    return [normalized]
        rest = rest[:-1]
    return []


def _resolve_php_import(source_path: str, raw_import: str, ctx: _ImportContext) -> List[str]:
    # include/require carregam um caminho de arquivo; `use` carrega um namespace PSR-4
    if raw_import.endswith(".php") or raw_import.startswith(("./", "../", "/")):
        base = posixpath.dirname(source_path)
        target = posixpath.normpath(posixpath.join(base, raw_import.lstrip("/")))
        return [target] if target in ctx.manifest_files and target != source_path else []

    parts = [part for part in raw_import.replace("\\", "/").split("/") if part]
    while parts:
        joined = "/".join(parts)
        for root in ctx.package_roots:
            prefix = posixpath.join(root, joined) if root else joined
            candidate = posixpath.normpath(f"{prefix}.php")
            if candidate in ctx.manifest_files and candidate != source_path:
                return [candidate]
        parts = parts[:-1]
    return []


def _resolve_ruby_import(source_path: str, raw_import: str, ctx: _ImportContext) -> List[str]:
    target = raw_import if raw_import.endswith(".rb") else f"{raw_import}.rb"
    # `require_relative` ancora no arquivo; `require` ancora no load path (as raízes)
    for base in (posixpath.dirname(source_path), *ctx.package_roots):
        candidate = posixpath.normpath(posixpath.join(base, target) if base else target)
        if candidate in ctx.manifest_files and candidate != source_path:
            return [candidate]
    return []


def _resolve_swift_import(source_path: str, raw_import: str, ctx: _ImportContext) -> List[str]:
    # Swift importa módulo, não arquivo: o módulo é o diretório de fontes homônimo
    module = raw_import.split(".")[0]
    targets: List[str] = []
    for directory in ctx.dirs_by_name.get(module, ()):
        targets.extend(_files_in_package_dir(ctx, directory, ".swift", source_path))
    return sorted(set(targets))


ImportResolver = Callable[[str, str, _ImportContext], List[str]]

# @MindContext: registry de resolução de import por sufixo (§3.3). O conjunto de
# LINGUAGENS coberto aqui tem de ser exatamente `IMPORT_RESOLUTION_TIERS["treesitter"]`
# — `tests/test_resolution_coverage.py` guarda essa igualdade.
_IMPORT_RESOLVERS: Dict[str, ImportResolver] = {
    ".py": _resolve_python_targets,
    ".js": _resolve_js_ts_targets,
    ".jsx": _resolve_js_ts_targets,
    ".ts": _resolve_js_ts_targets,
    ".tsx": _resolve_js_ts_targets,
    ".go": _resolve_go_import,
    ".java": _resolve_declared_namespace_import,
    ".cs": _resolve_declared_namespace_import,
    ".kt": _resolve_declared_namespace_import,
    ".kts": _resolve_declared_namespace_import,
    ".scala": _resolve_declared_namespace_import,
    ".rs": _resolve_rust_import,
    ".php": _resolve_php_import,
    ".rb": _resolve_ruby_import,
    ".swift": _resolve_swift_import,
}


def _resolve_import_targets(
    file_path: str, raw_import: str, ctx: _ImportContext
) -> List[str]:
    resolver = _IMPORT_RESOLVERS.get(posixpath.splitext(file_path)[1].lower())
    if resolver is None:
        return []
    return resolver(file_path, raw_import, ctx)


def count_unresolved_import_files(manifest, ctx: _ImportContext) -> int:
    """
    Arquivos que declaram import mas não produziram nenhuma aresta.

    É a métrica de `resolution_coverage.files_unresolved`: mede exatamente o modo de
    falha que a F3 existe para tornar visível — arquivo indexado, mas mudo sobre como
    se conecta. Fica igual nos dois caminhos de build por ser a única implementação.
    """
    unresolved = 0
    for file_path, raw_imports in manifest.files_imports.items():
        if not raw_imports:
            continue
        if not any(
            _resolve_import_targets(file_path, raw_import, ctx) for raw_import in raw_imports
        ):
            unresolved += 1
    return unresolved


def _build_resolution_coverage(languages: Iterable[str], files_unresolved: int) -> dict:
    """
    Bloco `resolution_coverage` do grafo (DECISÃO-005).

    Lista só as linguagens efetivamente presentes no índice e garante, por construção,
    que nenhuma apareça em dois tiers.
    """
    present = set(languages or ())
    coverage: Dict[str, Any] = {}
    assigned: set[str] = set()
    for tier in ("scip", "treesitter", "none"):
        tier_languages = sorted(
            (present & set(IMPORT_RESOLUTION_TIERS.get(tier, frozenset()))) - assigned
        )
        assigned.update(tier_languages)
        coverage[tier] = tier_languages
    coverage["files_unresolved"] = files_unresolved
    return coverage


def _node_summary(node: dict) -> dict:
    return {
        "id": node["id"],
        "label": node.get("label"),
        "kind": node.get("kind"),
        "file_path": node.get("file_path"),
        "lines": node.get("lines"),
        "degree": node.get("degree", 0),
    }


def is_noise_hub(node: dict) -> bool:
    # @MindWhy: denylist só no ranking/expansão; o nó permanece no grafo e no explain direto
    kind = node.get("kind")
    if kind in {"section", "rationale"}:
        return True
    label = str(node.get("label") or "").casefold()
    return label in GRAPH_NOISE_HUB_LABELS


def _build_adjacency(graph: dict) -> Dict[str, List[Tuple[str, str]]]:
    adjacency: Dict[str, List[Tuple[str, str]]] = {}
    for edge in graph.get("edges", []):
        adjacency.setdefault(edge["source"], []).append((edge["target"], edge["kind"]))
        adjacency.setdefault(edge["target"], []).append((edge["source"], edge["kind"]))
    return adjacency


def _build_reverse_adjacency(graph: dict) -> Dict[str, List[Tuple[str, str, dict]]]:
    """target → [(source, kind, edge), ...] — dependentes de cada dependência."""
    reverse: Dict[str, List[Tuple[str, str, dict]]] = {}
    for edge in graph.get("edges", []):
        reverse.setdefault(edge["target"], []).append((edge["source"], edge["kind"], edge))
    return reverse


def _via_location(dependent: dict, edge: dict) -> Optional[dict]:
    location = edge.get("location")
    if location:
        return location
    file_path = dependent.get("file_path")
    lines = dependent.get("lines")
    if file_path and lines:
        return {"file_path": file_path, "lines": lines}
    if file_path:
        return {"file_path": file_path}
    return None


def _serialized_len(payload: dict) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def _mark_graph_truncated(payload: dict, omitted: int = 1) -> None:
    warnings = set(payload.get("warnings") or [])
    warnings.add("truncated_for_budget")
    payload["warnings"] = sorted(warnings)
    mode = payload.get("mode")
    truncated = payload.get("truncated")
    if mode == "explain":
        current = dict(truncated) if isinstance(truncated, dict) else {}
        current["budget"] = current.get("budget", 0) + omitted
        payload["truncated"] = current
        return
    if mode == "affected" or isinstance(truncated, dict) and "omitted" in (truncated or {}):
        previous = truncated.get("omitted", 0) if isinstance(truncated, dict) else 0
        payload["truncated"] = {"omitted": previous + omitted}
        return
    payload["truncated"] = True


def graph_cut_once(payload: dict) -> bool:
    """
    Remove UM item do payload de `atlas_graph`, na mesma ordem de prioridade
    histórica (items -> neighbors -> rationale/notes -> path), e declara o
    corte em `truncated`/`warnings`. Retorna `False` quando não há mais nada a
    remover (payload já reduzido ao essencial) — é o `cut_once` que
    `response_budget.finalize_response` chama repetidamente até caber em
    chars/bytes/tokens (§4.2 do plano observabilidade-tokens-consultas).
    """
    items = payload.get("items")
    if isinstance(items, list) and items:
        items.pop()
        _mark_graph_truncated(payload)
        return True

    neighbors = payload.get("neighbors")
    if isinstance(neighbors, dict):
        for kind in list(neighbors):
            bucket = neighbors.get(kind)
            if isinstance(bucket, list) and bucket:
                bucket.pop()
                _mark_graph_truncated(payload)
                return True

    for key in ("rationale", "notes"):
        bucket = payload.get(key)
        if isinstance(bucket, list) and bucket:
            bucket.pop()
            _mark_graph_truncated(payload)
            return True

    path = payload.get("path")
    if isinstance(path, list) and len(path) > 1:
        path.pop()
        _mark_graph_truncated(payload)
        return True

    return False


def graph_minimal_envelope(payload: dict) -> dict:
    """
    Envelope mínimo de `atlas_graph`: preserva `mode` e a identidade do nó-alvo
    (já bounded por `_node_summary`), esvazia coleções e declara o corte —
    nunca finge ausência de correspondências (D3/guardrail do IPD).
    """
    minimal: Dict[str, Any] = {"mode": payload.get("mode")}
    for key in ("node", "target"):
        if key in payload:
            minimal[key] = payload[key]
    if "found" in payload:
        minimal["found"] = payload["found"]
        minimal["path"] = []
    if "items" in payload:
        minimal["items"] = []
    if "neighbors" in payload:
        minimal["neighbors"] = {}
        minimal["rationale"] = []
        minimal["notes"] = []
    warnings = set(payload.get("warnings") or [])
    warnings.add("truncated_for_budget")
    minimal["warnings"] = sorted(warnings)
    minimal["truncated"] = {"omitted": "all"}
    return minimal


def enforce_graph_response_budget(
    payload: dict, max_chars: int = GRAPH_RESPONSE_MAX_CHARS
) -> dict:
    """
    Pós-condição por caracteres apenas (compat/uso direto fora do servidor
    MCP): corte declarado em truncated/warnings. `atlas_graph` usa
    `response_budget.finalize_response` (chars + bytes + tokens exatos) com
    `graph_cut_once`/`graph_minimal_envelope` acima.
    """
    while _serialized_len(payload) > max_chars:
        if not graph_cut_once(payload):
            break
    return payload


def _attach_query_indexes(graph: dict) -> dict:
    graph["_nodes_by_id"] = {node["id"]: node for node in graph.get("nodes", [])}
    graph["_adjacency"] = _build_adjacency(graph)
    graph["_reverse_adjacency"] = _build_reverse_adjacency(graph)
    return graph


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
        "import_languages": sorted(IMPORT_RESOLUTION_TIERS["treesitter"]),
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
    import_context: _ImportContext,
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

    def _add_edge(source: str, target: str, kind: str, origin: Optional[str] = None) -> None:
        key = (source, target, kind)
        if source == target or key in edge_keys:
            return
        edge_keys.add(key)
        edge = {"source": source, "target": target, "kind": kind}
        # @MindWhy: `origin` só onde a qualidade da aresta varia (DECISÃO-006).
        # Em `contains`/`cites`/`links_to`/`annotates` não há tier a declarar, e o
        # campo só inflaria o graph.json, que é lido inteiro e cacheado em memória.
        if origin and kind in _ORIGIN_EDGE_KINDS:
            edge["origin"] = origin
        edges.append(edge)

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
        for target_path in _resolve_import_targets(file_path, raw_import, import_context):
            target_id = f"file:{target_path}"
            if target_id in nodes:
                _add_edge(source_id, target_id, "imports", origin=IMPORT_ORIGIN_TREESITTER)


HISTORY_NODE_KIND = "commit"
HISTORY_EDGE_KIND = "touches"
# Arestas fora do grau: `contains` é estrutura interna do arquivo e `touches` é
# história — nenhuma das duas é dependência entre símbolos.
_NON_DEGREE_EDGE_KINDS = frozenset({"contains", HISTORY_EDGE_KIND})


def commit_node_id(repo: str, sha: str) -> str:
    """Chave de nó do commit; inclui o repo para SHAs iguais não colidirem [GA-03]."""
    return f"{HISTORY_NODE_KIND}:{repo}:{sha}"


def _finalize_graph(
    graph: dict,
    languages: Optional[Iterable[str]] = None,
    files_unresolved: Optional[int] = None,
) -> dict:
    nodes = {node["id"]: dict(node) for node in graph.get("nodes", [])}
    edges = list(graph.get("edges", []))

    degree_by_id = {node_id: 0 for node_id in nodes}
    for edge in edges:
        # `touches` é relação histórica consultável, não dependência de código:
        # contá-la no grau moveria hubs, brief e a expansão estrutural [GA-05]
        if edge["kind"] in _NON_DEGREE_EDGE_KINDS:
            continue
        degree_by_id[edge["source"]] = degree_by_id.get(edge["source"], 0) + 1
        degree_by_id[edge["target"]] = degree_by_id.get(edge["target"], 0) + 1
    for node_id, degree in degree_by_id.items():
        nodes[node_id]["degree"] = degree

    top_hubs = sorted(
        (
            {"id": node_id, "degree": degree}
            for node_id, degree in degree_by_id.items()
            if node_id in nodes
            and nodes[node_id].get("kind") != HISTORY_NODE_KIND
            and not is_noise_hub(nodes[node_id])
        ),
        key=lambda item: (-item["degree"], item["id"]),
    )[:GRAPH_TOP_HUBS_LIMIT]

    graph["nodes"] = sorted(nodes.values(), key=lambda node: node["id"])
    graph["edges"] = sorted(edges, key=lambda edge: (edge["source"], edge["target"], edge["kind"]))
    graph["metrics"] = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "top_hubs": top_hubs,
    }
    # @MindWhy: `origin` não entra em `degree` nem em `top_hubs` — §1.2/§1.3 não podem
    # regredir. O bloco de cobertura é anexado depois das métricas, não dentro delas.
    if languages is not None:
        graph["resolution_coverage"] = _build_resolution_coverage(
            languages, files_unresolved or 0
        )
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


def build_and_write(
    storage,
    manifest,
    index_path: Path,
    return_metadata: bool = False,
    workspace_root: Optional[Path] = None,
):
    """
    Reconstrói `graph.json` inteiro a partir do estado atual do índice.

    `workspace_root` é opcional e serve só para descobrir manifestos de build que o
    índice não contém (`go.mod`, `*.csproj`); sem ele a resolução Go degrada para
    nenhuma aresta, o que `resolution_coverage` declara.
    """
    rows = storage.get_graph_projection()
    manifest_files = set(manifest.files.keys())
    name_to_paths = _stem_to_paths(manifest)
    package_roots = infer_package_roots(manifest_files, workspace_root)
    import_context = _ImportContext(
        manifest_files=manifest_files,
        package_roots=package_roots,
        files_declares=dict(manifest.files_declares),
        go_modules=infer_go_modules(manifest_files, workspace_root),
    )
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
            import_context=import_context,
        )

    graph = _finalize_graph(
        _build_empty_graph(manifest) | {"nodes": list(nodes.values()), "edges": edges},
        languages=manifest.languages_indexed,
        files_unresolved=count_unresolved_import_files(manifest, import_context),
    )
    graph_path, metadata = _persist_graph(graph, index_path)
    if return_metadata:
        return graph_path, metadata
    return graph_path


def build_and_write_incremental(
    index_path: Path,
    manifest,
    updated_chunks: List[CodeChunk],
    updated_file_paths: set[str],
    workspace_root: Optional[Path] = None,
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
    package_roots = infer_package_roots(manifest_files, workspace_root)
    import_context = _ImportContext(
        manifest_files=manifest_files,
        package_roots=package_roots,
        files_declares=dict(manifest.files_declares),
        go_modules=infer_go_modules(manifest_files, workspace_root),
    )
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
            import_context=import_context,
        )

    updated_graph = _build_empty_graph(manifest) | {
        "nodes": list(nodes.values()),
        "edges": kept_edges,
    }
    updated_graph = _finalize_graph(
        updated_graph,
        languages=manifest.languages_indexed,
        files_unresolved=count_unresolved_import_files(manifest, import_context),
    )
    _carry_over_scip_block(graph, updated_graph)
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
    _attach_query_indexes(graph)
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


def _promote_scip_languages(graph: dict, languages: Iterable[str]) -> None:
    """Move para o tier `scip` quem a ingestão cobriu — nunca deixa em dois tiers."""
    coverage = graph.get("resolution_coverage")
    if not isinstance(coverage, dict):
        return
    promoted = set(languages)
    if not promoted:
        return
    coverage = dict(coverage)
    for tier in ("treesitter", "none"):
        coverage[tier] = [lang for lang in coverage.get(tier, []) if lang not in promoted]
    coverage["scip"] = sorted(set(coverage.get("scip", [])) | promoted)
    graph["resolution_coverage"] = coverage


def _carry_over_scip_block(previous: dict, graph: dict) -> None:
    """
    Mantém a declaração da última ingestão SCIP através de um rebuild incremental.

    `_build_empty_graph` cria um grafo do zero, então sem isto o bloco `scip` e o
    tier de cobertura sumiriam a cada indexação incremental — o índice passaria a
    se apresentar como se nunca tivesse tido resolução semântica (Princípio VI).
    `edges` é recontado sobre o grafo novo: arestas `calls` de arquivos alterados
    caem junto com os nós que foram re-chunkados.
    """
    scip = previous.get("scip")
    if not isinstance(scip, dict):
        return
    surviving = sum(1 for edge in graph.get("edges", []) if edge.get("kind") == "calls")
    graph["scip"] = {**scip, "edges": surviving}
    if scip.get("status") == "ok":
        _promote_scip_languages(graph, scip.get("languages", []))


def apply_scip_result(
    index_path: Path,
    call_edges: List[dict],
    status: str,
    head_sha: Optional[str],
    languages: Iterable[str],
) -> tuple[Path, dict]:
    """
    Aplica o resultado da ingestão SCIP ao `graph.json` já persistido (DECISÃO-002).

    Substitui **apenas** as arestas `kind="calls"`; as demais seguem intactas. Em
    status diferente de `ok` nada é reescrito além do bloco `graph["scip"]`, que
    declara a degradação sem descartar as arestas da ingestão anterior (Princípio VI).
    """
    # As chaves `_*` são índices de consulta anexados por `load_graph`; ficam fora
    # do que é persistido, e a cópia evita mutar o dict que está no cache.
    graph = {key: value for key, value in load_graph(index_path).items() if not key.startswith("_")}
    previous = graph.get("scip") or {}

    if status == "ok":
        kept_edges = [edge for edge in graph.get("edges", []) if edge.get("kind") != "calls"]
        graph["edges"] = kept_edges + [dict(edge) for edge in call_edges]
        ingested_languages = sorted(set(languages))
        graph["scip"] = {
            "status": status,
            "head_sha": head_sha,
            "languages": ingested_languages,
            "edges": len(call_edges),
        }
        _promote_scip_languages(graph, ingested_languages)
    else:
        graph["scip"] = {
            "status": status,
            "head_sha": previous.get("head_sha"),
            "languages": list(previous.get("languages", [])),
            "edges": int(previous.get("edges", 0)),
        }

    return _persist_graph(_finalize_graph(graph), index_path)


def apply_history(index_path: Path, commit_rows: Iterable[dict]) -> tuple[Path, dict]:
    """
    Reprojeta a camada histórica sobre o `graph.json` já persistido (mesmo padrão
    de `apply_scip_result`): substitui TODOS os nós `commit` e arestas `touches`
    pela visão informada, sem tocar nas relações estruturais.

    A `location` da aresta é o intervalo ATUAL do símbolo — é o ponto verificável
    contra o índice corrente; símbolo ausente hoje simplesmente não recebe aresta.
    """
    graph = {key: value for key, value in load_graph(index_path).items() if not key.startswith("_")}
    nodes = [
        node for node in graph.get("nodes", []) if node.get("kind") != HISTORY_NODE_KIND
    ]
    edges = [
        edge for edge in graph.get("edges", []) if edge.get("kind") != HISTORY_EDGE_KIND
    ]
    nodes_by_id = {node["id"]: node for node in nodes}

    for row in commit_rows:
        node_id = commit_node_id(row["repo"], row["id"])
        nodes.append(
            {
                "id": node_id,
                "kind": HISTORY_NODE_KIND,
                "label": row.get("subject") or "",
                "file_path": None,
                "lines": None,
            }
        )
        touches = row.get("touches")
        if touches is None:
            touches = json.loads(row.get("touches_json") or "[]")
        seen: set[str] = set()
        for item in touches:
            symbol_id = f"sym:{item['file_path']}#{item['scope_name']}"
            target = nodes_by_id.get(symbol_id)
            if target is None or symbol_id in seen:
                continue
            seen.add(symbol_id)
            edges.append(
                {
                    "source": node_id,
                    "target": symbol_id,
                    "kind": HISTORY_EDGE_KIND,
                    "location": {
                        "file_path": target.get("file_path"),
                        "lines": target.get("lines"),
                    },
                }
            )

    graph["nodes"] = nodes
    graph["edges"] = edges
    return _persist_graph(_finalize_graph(graph), index_path)


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
    result = []
    for item in graph.get("metrics", {}).get("top_hubs", []):
        if len(result) >= top_n:
            break
        node = graph["_nodes_by_id"].get(item["id"])
        if node is None or is_noise_hub(node):
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


def _origins_by_edge(graph: dict) -> Dict[Tuple[str, str, str], str]:
    """Origem por aresta nos dois sentidos — `_adjacency` é não-dirigida."""
    origins: Dict[Tuple[str, str, str], str] = {}
    for edge in graph.get("edges", []):
        if edge.get("kind") not in _ORIGIN_EDGE_KINDS:
            continue
        origin = edge.get("origin") or EDGE_ORIGIN_UNKNOWN
        origins[(edge["source"], edge["target"], edge["kind"])] = origin
        origins[(edge["target"], edge["source"], edge["kind"])] = origin
    return origins


def explain(graph: dict, ref: str) -> dict:
    node = resolve_node(graph, ref)
    adjacency = graph["_adjacency"]
    origins = _origins_by_edge(graph)
    neighbors: Dict[str, List[dict]] = {}

    for neighbor_id, edge_kind in adjacency.get(node["id"], []):
        neighbor = graph["_nodes_by_id"][neighbor_id]
        summary = _node_summary(neighbor)
        summary["edge_kind"] = edge_kind
        # Grafo 2.1.0 não grava `origin`; o consumidor recebe "unknown" (DECISÃO-006)
        if edge_kind in _ORIGIN_EDGE_KINDS:
            summary["origin"] = origins.get(
                (node["id"], neighbor_id, edge_kind), EDGE_ORIGIN_UNKNOWN
            )
        neighbors.setdefault(neighbor["kind"], []).append(summary)

    truncated: Dict[str, int] = {}
    capped: Dict[str, List[dict]] = {}
    for kind, items in neighbors.items():
        ordered = sorted(
            items,
            key=lambda item: (-(item.get("degree") or 0), item.get("label") or "", item["id"]),
        )
        omitted = max(0, len(ordered) - GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND)
        if omitted:
            truncated[kind] = omitted
        capped[kind] = ordered[:GRAPH_EXPLAIN_MAX_NEIGHBORS_PER_KIND]

    notes = sorted(
        [
            {
                "id": item["id"],
                "label": item.get("label"),
                "file_path": item.get("file_path"),
                "lines": item.get("lines"),
            }
            for item in capped.get("doc", [])
        ],
        key=lambda item: item["file_path"] or "",
    )

    result = {
        "node": _node_summary(node),
        "neighbors": capped,
        "rationale": list(capped.get("rationale", [])),
        "notes": notes,
    }
    if truncated:
        result["truncated"] = truncated
    return result


_AFFECTED_RELATIONS = frozenset({"calls", "imports"})


def affected(graph: dict, ref: str) -> dict:
    # @MindFlow: resolve → semeia contains (não reporta) → BFS reversa {calls,imports} → cap
    # @MindRisk: reusar _adjacency não-dirigida marcaria dependência e dependente como afetados
    seed = resolve_node(graph, ref)
    nodes_by_id = graph["_nodes_by_id"]
    reverse = graph.get("_reverse_adjacency")
    if reverse is None:
        reverse = _build_reverse_adjacency(graph)

    warnings: List[str] = []
    if not any(edge.get("kind") == "calls" for edge in graph.get("edges", [])):
        warnings.append("calls_unavailable")

    seed_ids = {seed["id"]}
    for edge in graph.get("edges", []):
        if edge["kind"] == "contains" and edge["source"] == seed["id"]:
            seed_ids.add(edge["target"])

    visited = set(seed_ids)
    queue: deque[tuple[str, int, str, Optional[dict], str]] = deque()

    def enqueue_from(origin_id: str, hops: int) -> None:
        origin = nodes_by_id.get(origin_id)
        if origin is None:
            return
        if is_noise_hub(origin) and origin_id != seed["id"]:
            return
        for neighbor_id, kind, edge in reverse.get(origin_id, []):
            if kind not in _AFFECTED_RELATIONS or neighbor_id in visited:
                continue
            neighbor = nodes_by_id.get(neighbor_id)
            if neighbor is None:
                continue
            if is_noise_hub(neighbor) and neighbor_id != seed["id"]:
                continue
            visited.add(neighbor_id)
            queue.append(
                (
                    neighbor_id,
                    hops,
                    kind,
                    _via_location(neighbor, edge),
                    edge.get("origin") or EDGE_ORIGIN_UNKNOWN,
                )
            )

    for sid in seed_ids:
        enqueue_from(sid, 1)

    items: List[dict] = []
    while queue:
        node_id, hops, via, location, origin = queue.popleft()
        neighbor = nodes_by_id[node_id]
        entry = {**_node_summary(neighbor), "hops": hops, "via": via, "origin": origin}
        if location:
            entry["via_location"] = location
        items.append(entry)
        enqueue_from(node_id, hops + 1)

    items.sort(key=lambda item: (item["hops"], -(item.get("degree") or 0), item["id"]))
    omitted = max(0, len(items) - GRAPH_AFFECTED_MAX_RESULTS)
    items = items[:GRAPH_AFFECTED_MAX_RESULTS]
    return {
        "target": _node_summary(seed),
        "items": items,
        "truncated": {"omitted": omitted} if omitted else False,
        "warnings": warnings,
    }
