"""
Briefing pré-computado do projeto (`brief.json`), consumido pela tool MCP `atlas_brief`.

Diferente de uma listagem exaustiva, o brief tem **custo de token com teto fixo**: toda
lista é ranqueada e capada por constantes `BRIEF_*`, de modo que o tamanho da resposta não
cresce com o tamanho do repositório.

Este módulo NÃO acessa o `StorageBackend`: tudo é derivado de `manifest` + `graph.json`,
o que mantém o caminho de recomputação sob demanda barato em repositórios de qualquer
tamanho (zero queries LanceDB) [D].
"""

import json
import os
import posixpath
import re
import sys
import threading
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from codesteer_atlas.config import (
    BRIEF_ENTRYPOINT_PROBE_LIMIT,
    BRIEF_ENTRYPOINT_PROBE_MAX_BYTES,
    BRIEF_FILENAME,
    BRIEF_LAYER_SPLIT_MAX,
    BRIEF_LAYER_TOP_FILES,
    BRIEF_LEVEL0_MAX_CHARS,
    BRIEF_LEVEL1_MAX_CHARS,
    BRIEF_MAX_ENTRYPOINTS,
    BRIEF_MAX_HUBS,
    BRIEF_MAX_LANGUAGES,
    BRIEF_MAX_LAYERS,
    BRIEF_MAX_PATH_CHARS,
    GRAPH_FILENAME,
    SUPPORTED_EXTENSIONS,
)
from codesteer_atlas.graph import is_noise_hub, load_graph, resolve_module_path

BRIEF_SCHEMA_VERSION = "1.0"

_BRIEF_CACHE_LOCK = threading.Lock()
_BRIEF_CACHE: Dict[str, Any] = {
    "path": None,
    "mtime_ns": None,
    "size": None,
    "brief": None,
}

# Diretórios que agrupam código sem serem, eles próprios, uma camada informativa:
# `src/` sozinho não diz nada, `src/codesteer_atlas` diz
CONTAINER_DIRS = {
    "src",
    "app",
    "apps",
    "packages",
    "lib",
    "libs",
    "pkg",
    "cmd",
    "internal",
    "modules",
    "services",
    "components",
}

_ROOT_LAYER = "(root)"

# Linguagens que não caracterizam a stack do projeto ao escolher `primary_language`
_NON_PRIMARY_LANGUAGES = {
    "markdown",
    "text",
    "json",
    "yaml",
    "toml",
    "xml",
    "html",
    "css",
    "scss",
}

_DOC_LANGUAGES = {"markdown", "text"}

_ROLE_SEGMENTS: List[Tuple[str, set]] = [
    ("tests", {"tests", "test", "spec", "specs", "__tests__", "e2e"}),
    ("vendor", {"vendor", "third_party", "thirdparty", "node_modules", "dist", "build"}),
    ("docs", {"docs", "doc", "adr", "rfc", "cognitive-base", ".memory-bank"}),
    ("scripts", {"scripts", "tools", "bin", "ci", ".github"}),
]

# Prioridade de camada no ranking de entrypoints: quanto maior, mais provável de ser
# o entrypoint "de verdade" do projeto
_LAYER_PRIORITY = {"source": 3, "scripts": 2, "root": 1}

# Basenames que sugerem entrypoint mesmo sem nenhuma aresta de import resolvida.
# É o fallback que mantém a detecção viva quando a topologia de imports está vazia [D]
_ENTRYPOINT_BASENAMES = {
    "main.py",
    "__main__.py",
    "cli.py",
    "app.py",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "server.py",
    "index.ts",
    "index.js",
    "main.go",
    "main.rs",
    "Program.cs",
}

_EXCLUDED_ENTRYPOINT_BASENAMES = {"__init__.py", "conftest.py"}
_ENTRYPOINT_EXCLUDED_ROLES = {"tests", "docs", "vendor"}

_MAIN_GUARD_PATTERNS = [
    re.compile(r"^\s*if\s+__name__\s*==\s*[\"']__main__[\"']", re.MULTILINE),
    re.compile(r"^\s*func\s+main\s*\(", re.MULTILINE),
    re.compile(r"^\s*(?:public\s+)?static\s+.*\bMain\s*\(", re.MULTILINE),
]

_DOCKER_ENTRYPOINT_RE = re.compile(r"^\s*(ENTRYPOINT|CMD)\s+(.+)$", re.MULTILINE | re.IGNORECASE)


# ---------------------------------------------------------------------------
# Persistência e cache (espelha o padrão de graph.py)
# ---------------------------------------------------------------------------


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def _clear_brief_cache(brief_path: Optional[Path] = None) -> None:
    with _BRIEF_CACHE_LOCK:
        cached_path = _BRIEF_CACHE.get("path")
        if brief_path is not None and cached_path is not None and Path(cached_path) != Path(brief_path):
            return
        _BRIEF_CACHE.update({"path": None, "mtime_ns": None, "size": None, "brief": None})


def _brief_metadata(brief: dict, index_path: Path) -> dict:
    brief_path = Path(index_path) / BRIEF_FILENAME
    return {
        "brief_bytes": brief_path.stat().st_size if brief_path.exists() else 0,
        "brief_layers": len(brief.get("layers", [])),
        "brief_entrypoints": len(brief.get("entrypoints", [])),
    }


def _persist_brief(brief: dict, index_path: Path) -> tuple[Path, dict]:
    index_path = Path(index_path)
    index_path.mkdir(parents=True, exist_ok=True)
    brief_path = index_path / BRIEF_FILENAME
    _write_json_atomic(brief_path, brief)
    _clear_brief_cache(brief_path)
    return brief_path, _brief_metadata(brief, index_path)


def load_brief(index_dir: Path) -> Optional[dict]:
    """
    Lê `brief.json` do índice, com cache invalidado por mtime/size.

    Retorna `None` quando o arquivo não existe ou está ilegível/incompatível — o chamador
    decide se recomputa. Diferente de `load_graph`, ausência aqui não é erro: `atlas_brief`
    é a porta de entrada e precisa funcionar sobre índices gerados por versões anteriores.
    """
    brief_path = Path(index_dir) / BRIEF_FILENAME
    if not brief_path.exists():
        return None

    try:
        stat = brief_path.stat()
    except OSError:
        return None

    resolved = str(brief_path.resolve())
    with _BRIEF_CACHE_LOCK:
        if (
            _BRIEF_CACHE.get("path") == resolved
            and _BRIEF_CACHE.get("mtime_ns") == stat.st_mtime_ns
            and _BRIEF_CACHE.get("size") == stat.st_size
            and _BRIEF_CACHE.get("brief") is not None
        ):
            return _BRIEF_CACHE["brief"]

    try:
        with open(brief_path, "r", encoding="utf-8") as f:
            brief = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(brief, dict) or brief.get("brief_version") != BRIEF_SCHEMA_VERSION:
        return None

    with _BRIEF_CACHE_LOCK:
        _BRIEF_CACHE.update(
            {
                "path": resolved,
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "brief": brief,
            }
        )
    return brief


# ---------------------------------------------------------------------------
# Helpers de path e camada
# ---------------------------------------------------------------------------


def _elide_path(path: str) -> str:
    if len(path) <= BRIEF_MAX_PATH_CHARS:
        return path
    keep = BRIEF_MAX_PATH_CHARS - 3
    head = keep // 2
    tail = keep - head
    return f"{path[:head]}...{path[-tail:]}"


def _language_of(file_path: str) -> Optional[str]:
    ext = posixpath.splitext(file_path)[1].lower()
    return SUPPORTED_EXTENSIONS.get(ext)


def _layer_key(file_path: str, container_children: Dict[str, int]) -> str:
    parts = file_path.split("/")
    if len(parts) == 1:
        return _ROOT_LAYER
    if (
        parts[0] in CONTAINER_DIRS
        and len(parts) > 2
        and container_children.get(parts[0], 0) <= BRIEF_LAYER_SPLIT_MAX
    ):
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _container_children(file_paths) -> Dict[str, int]:
    children: Dict[str, set] = {}
    for file_path in file_paths:
        parts = file_path.split("/")
        if len(parts) > 2 and parts[0] in CONTAINER_DIRS:
            children.setdefault(parts[0], set()).add(parts[1])
    return {name: len(values) for name, values in children.items()}


def _classify_layer_role(layer_path: str, lang_counts: Counter) -> str:
    if layer_path == _ROOT_LAYER:
        return "root"
    segments = {segment.lower() for segment in layer_path.split("/")}
    for role, markers in _ROLE_SEGMENTS:
        if segments & markers:
            return role
    total = sum(lang_counts.values())
    if total and sum(lang_counts[lang] for lang in _DOC_LANGUAGES) / total >= 0.8:
        return "docs"
    return "source"


# ---------------------------------------------------------------------------
# Extração de sinais do grafo
# ---------------------------------------------------------------------------


def _per_file_stats_from_graph(graph: Optional[dict]) -> Dict[str, Dict[str, int]]:
    """
    Deriva, por arquivo, quantos símbolos ele contém, seu grau de conectividade e sua
    última linha conhecida. `lines` só existe nos nós filhos, daí o max sobre eles.
    """
    stats: Dict[str, Dict[str, int]] = {}
    if not graph:
        return stats

    for node in graph.get("nodes", []):
        file_path = node.get("file_path")
        if not file_path:
            continue
        entry = stats.setdefault(file_path, {"sym": 0, "deg": 0, "loc": 0})
        kind = node.get("kind")
        if kind in {"file", "doc"}:
            entry["deg"] = node.get("degree", 0) or 0
        elif kind == "symbol":
            entry["sym"] += 1
        lines = node.get("lines")
        if isinstance(lines, list) and len(lines) == 2 and isinstance(lines[1], int):
            entry["loc"] = max(entry["loc"], lines[1])
    return stats


def _import_edge_count(graph: Optional[dict]) -> int:
    if not graph:
        return 0
    return sum(1 for edge in graph.get("edges", []) if edge.get("kind") == "imports")


def _compute_hubs(graph: Optional[dict]) -> List[dict]:
    """
    Recalcula os hubs a partir de `nodes[].degree`, filtrando ruído.

    Não reutiliza `metrics.top_hubs` porque ele é capado ANTES de qualquer filtro: as
    primeiras posições costumam ser nós `section`/`rationale`, que não orientam ninguém.
    """
    if not graph:
        return []

    candidates = [
        node
        for node in graph.get("nodes", [])
        if node.get("kind") in {"file", "doc", "symbol"}
        and (node.get("degree") or 0) >= 1
        and not is_noise_hub(node)
    ]
    candidates.sort(key=lambda node: (-(node.get("degree") or 0), node["id"]))
    return [
        {
            "id": _elide_path(node["id"]),
            "label": node.get("label"),
            "kind": node.get("kind"),
            "degree": node.get("degree") or 0,
        }
        for node in candidates[:BRIEF_MAX_HUBS]
    ]


# ---------------------------------------------------------------------------
# Entrypoints
# ---------------------------------------------------------------------------


def _resolve_module_attr(module: str, manifest_files: set) -> Optional[str]:
    """
    Resolve `pacote.modulo` para um path do manifest.

    Delega a resolução por raízes de pacote ao helper compartilhado do grafo e só
    então recorre a um casamento por sufixo — desempate aceitável aqui porque um
    entrypoint declarado já é evidência forte, mas que o grafo não usa para não
    inventar arestas.
    """
    resolved = resolve_module_path(module, set(manifest_files))
    if resolved is not None:
        return resolved

    suffix = f"/{module.replace('.', '/')}.py"
    matches = sorted(path for path in manifest_files if path.endswith(suffix))
    if len(matches) == 1:
        return matches[0]
    return None


def _read_text(path: Path, max_bytes: Optional[int] = None) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read() if max_bytes is None else f.read(max_bytes)
    except OSError:
        return None


def _declared_entrypoints(workspace_root: Path, manifest_files: set, layer_paths: List[str]) -> List[dict]:
    found: List[dict] = []
    roots = [""] + [layer for layer in layer_paths if layer != _ROOT_LAYER]

    for rel_root in roots:
        base = workspace_root / rel_root if rel_root else workspace_root

        pyproject = base / "pyproject.toml"
        if pyproject.exists():
            try:
                with open(pyproject, "rb") as f:
                    data = tomllib.load(f)
                project = data.get("project") or {}
                scripts: dict = {}
                scripts.update(project.get("scripts") or {})
                scripts.update(project.get("gui-scripts") or {})
                for name, target in sorted(scripts.items()):
                    if not isinstance(target, str):
                        continue
                    module, _, attr = target.partition(":")
                    file_path = _resolve_module_attr(module, manifest_files)
                    if file_path is None:
                        continue
                    found.append(
                        {
                            "file_path": file_path,
                            "symbol": attr or None,
                            "kind": "console_script",
                            "name": name,
                            "confidence": "declared",
                            "evidence": "pyproject.toml [project.scripts]",
                        }
                    )
            except (OSError, tomllib.TOMLDecodeError):
                pass

        package_json = base / "package.json"
        if package_json.exists():
            raw = _read_text(package_json)
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {}
            if isinstance(data, dict):
                bins = data.get("bin")
                entries: List[Tuple[str, str]] = []
                if isinstance(bins, str):
                    entries.append((data.get("name") or "bin", bins))
                elif isinstance(bins, dict):
                    entries.extend((k, v) for k, v in sorted(bins.items()) if isinstance(v, str))
                for name, target in entries:
                    rel = posixpath.normpath(posixpath.join(rel_root, target.lstrip("./")))
                    found.append(
                        {
                            "file_path": rel if rel in manifest_files else target,
                            "symbol": None,
                            "kind": "npm_bin",
                            "name": name,
                            "confidence": "declared",
                            "evidence": "package.json bin",
                        }
                    )
                npm_scripts = data.get("scripts")
                if isinstance(npm_scripts, dict):
                    for name in ("start", "dev", "serve"):
                        command = npm_scripts.get(name)
                        if isinstance(command, str):
                            found.append(
                                {
                                    "file_path": posixpath.join(rel_root, "package.json").lstrip("/"),
                                    "symbol": None,
                                    "kind": "npm_script",
                                    "name": f"npm run {name}",
                                    "confidence": "declared",
                                    "evidence": f"package.json scripts.{name}: {command[:60]}",
                                }
                            )

        dockerfile = base / "Dockerfile"
        if dockerfile.exists():
            raw = _read_text(dockerfile, BRIEF_ENTRYPOINT_PROBE_MAX_BYTES) or ""
            matches = _DOCKER_ENTRYPOINT_RE.findall(raw)
            if matches:
                directive, command = matches[-1]
                found.append(
                    {
                        "file_path": posixpath.join(rel_root, "Dockerfile").lstrip("/"),
                        "symbol": None,
                        "kind": "container",
                        "name": command.strip()[:80],
                        "confidence": "declared",
                        "evidence": f"Dockerfile {directive.upper()}",
                    }
                )

    return found


def _has_main_guard(path: Path) -> bool:
    content = _read_text(path, BRIEF_ENTRYPOINT_PROBE_MAX_BYTES)
    if content is None:
        return False
    return any(pattern.search(content) for pattern in _MAIN_GUARD_PATTERNS)


def _candidate_entrypoint_files(
    manifest_files: set,
    graph: Optional[dict],
    role_by_file: Dict[str, str],
) -> List[Tuple[str, int]]:
    """
    Monta o conjunto de candidatos a entrypoint inferido, já filtrado, e devolve
    `(path, out_degree)` para permitir ranqueamento antes do probe em disco.
    """
    out_degree: Counter = Counter()
    in_degree: Counter = Counter()
    if graph:
        for edge in graph.get("edges", []):
            if edge.get("kind") != "imports":
                continue
            source = edge.get("source", "")
            target = edge.get("target", "")
            if source.startswith("file:"):
                out_degree[source[len("file:") :]] += 1
            if target.startswith("file:"):
                in_degree[target[len("file:") :]] += 1

    candidates: Dict[str, int] = {}
    for file_path in manifest_files:
        basename = posixpath.basename(file_path)
        if basename in _EXCLUDED_ENTRYPOINT_BASENAMES:
            continue
        if role_by_file.get(file_path) in _ENTRYPOINT_EXCLUDED_ROLES:
            continue

        # Raiz de import: ninguém o importa, mas ele importa alguém
        is_import_root = out_degree[file_path] > 0 and in_degree[file_path] == 0
        if is_import_root or basename in _ENTRYPOINT_BASENAMES:
            candidates[file_path] = out_degree[file_path]

    ordered = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
    return ordered[:BRIEF_ENTRYPOINT_PROBE_LIMIT]


def _dedupe_entrypoints(items: List[dict]) -> List[dict]:
    """
    Vários aliases declarados costumam apontar para o mesmo arquivo (ex.: `indexer` e
    `atlas-index` → `indexer:cli`). Colapsa em uma entrada e junta os nomes, para não
    gastar slots do orçamento repetindo o mesmo alvo.
    """
    merged: Dict[tuple, dict] = {}
    for item in items:
        key = (item["file_path"], item.get("symbol"), item["kind"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(item)
            continue
        names = [name.strip() for name in existing["name"].split(",")]
        if item["name"] not in names:
            existing["name"] = ", ".join(sorted(names + [item["name"]]))
    return list(merged.values())


def _compute_entrypoints(
    workspace_root: Path,
    manifest_files: set,
    graph: Optional[dict],
    layer_paths: List[str],
    role_by_file: Dict[str, str],
) -> List[dict]:
    declared = _dedupe_entrypoints(_declared_entrypoints(workspace_root, manifest_files, layer_paths))

    inferred: List[dict] = []
    declared_paths = {item["file_path"] for item in declared}
    for file_path, _out_degree in _candidate_entrypoint_files(manifest_files, graph, role_by_file):
        if file_path in declared_paths:
            continue
        # Candidato que não passa no probe é DESCARTADO, não rebaixado: lista vazia
        # é resposta melhor do que um entrypoint inventado
        if not _has_main_guard(workspace_root / file_path):
            continue
        inferred.append(
            {
                "file_path": file_path,
                "symbol": None,
                "kind": "main_guard",
                "name": posixpath.splitext(posixpath.basename(file_path))[0],
                "confidence": "inferred",
                "evidence": "__main__ guard",
            }
        )

    def _sort_key(item: dict) -> tuple:
        tier = 0 if item["confidence"] == "declared" else 1
        role = role_by_file.get(item["file_path"], "source")
        return (tier, -_LAYER_PRIORITY.get(role, 0), item["file_path"])

    # Declarados nunca são despejados por inferidos
    ordered = sorted(declared, key=_sort_key) + sorted(inferred, key=_sort_key)
    for item in ordered:
        item["file_path"] = _elide_path(item["file_path"])
    return ordered[:BRIEF_MAX_ENTRYPOINTS]


# ---------------------------------------------------------------------------
# Seções derivadas do manifest
# ---------------------------------------------------------------------------


def _compute_identity(manifest, graph: Optional[dict], warnings: List[str]) -> dict:
    file_paths = list(manifest.files.keys())
    total_files = len(file_paths)

    # Denominador é ARQUIVO, não chunk: contagem de chunks é distorcida pela granularidade
    # do chunker (seções markdown vs símbolos) e reportaria a stack errada [D]
    lang_counts: Counter = Counter()
    for file_path in file_paths:
        language = _language_of(file_path)
        if language:
            lang_counts[language] += 1

    ranked = sorted(lang_counts.items(), key=lambda item: (-item[1], item[0]))
    languages = [
        {"name": name, "files": count, "pct": round(100 * count / total_files) if total_files else 0}
        for name, count in ranked[:BRIEF_MAX_LANGUAGES]
    ]

    primary_language = next(
        (name for name, _ in ranked if name not in _NON_PRIMARY_LANGUAGES),
        ranked[0][0] if ranked else None,
    )

    doc_files = sum(lang_counts[lang] for lang in _DOC_LANGUAGES)

    identity = {
        "repo": manifest.repos_indexed[0] if manifest.repos_indexed else "",
        "files": total_files,
        "chunks": manifest.total_chunks,
        "primary_language": primary_language,
        "doc_ratio": round(doc_files / total_files, 2) if total_files else 0.0,
        "languages": languages,
    }

    if graph is not None:
        symbol_count = sum(1 for node in graph.get("nodes", []) if node.get("kind") == "symbol")
        # Ausência de grafo omite o campo; zero com grafo presente é medição real
        identity["symbols"] = symbol_count
        code_files = sum(
            count for name, count in lang_counts.items() if name not in _NON_PRIMARY_LANGUAGES
        )
        if code_files > 0 and symbol_count == 0:
            warnings.append("low_symbol_coverage")

    if len(manifest.repos_indexed) > 1:
        identity["repos"] = list(manifest.repos_indexed)
        warnings.append("multi_repo_index")

    return identity


def _compute_layers(
    manifest,
    per_file: Dict[str, Dict[str, int]],
    warnings: List[str],
) -> Tuple[List[dict], int, Dict[str, str]]:
    file_paths = list(manifest.files.keys())
    container_children = _container_children(file_paths)

    if any(count > BRIEF_LAYER_SPLIT_MAX for count in container_children.values()):
        warnings.append("layers_collapsed")

    grouped: Dict[str, List[str]] = {}
    for file_path in file_paths:
        grouped.setdefault(_layer_key(file_path, container_children), []).append(file_path)

    role_by_layer: Dict[str, str] = {}
    role_by_file: Dict[str, str] = {}
    entries: List[dict] = []

    for layer_path, files in grouped.items():
        lang_counts: Counter = Counter()
        for file_path in files:
            language = _language_of(file_path)
            if language:
                lang_counts[language] += 1

        role = _classify_layer_role(layer_path, lang_counts)
        role_by_layer[layer_path] = role
        for file_path in files:
            role_by_file[file_path] = role

        entry = {
            "path": layer_path,
            "role": role,
            "files": len(files),
            "languages": [name for name, _ in lang_counts.most_common(3)],
        }

        if per_file:
            entry["symbols"] = sum(per_file.get(f, {}).get("sym", 0) for f in files)
            ranked_files = sorted(
                files,
                key=lambda f: (
                    -per_file.get(f, {}).get("deg", 0),
                    -per_file.get(f, {}).get("sym", 0),
                    -per_file.get(f, {}).get("loc", 0),
                    f,
                ),
            )[:BRIEF_LAYER_TOP_FILES]

            top: List[dict] = []
            for file_path in ranked_files:
                stats = per_file.get(file_path, {})
                relative = file_path
                if layer_path != _ROOT_LAYER and file_path.startswith(f"{layer_path}/"):
                    relative = file_path[len(layer_path) + 1 :]
                top.append(
                    {
                        "p": _elide_path(relative),
                        "sym": stats.get("sym", 0),
                        "deg": stats.get("deg", 0),
                    }
                )
            entry["top"] = top
            # Informa qual critério de fato decidiu a ordem, para o consumidor saber
            # o quanto confiar no ranking
            if any(item["deg"] > 0 for item in top):
                entry["rank_basis"] = "degree"
            elif any(item["sym"] > 0 for item in top):
                entry["rank_basis"] = "symbols"
            else:
                entry["rank_basis"] = "path"

        entries.append(entry)

    entries.sort(key=lambda item: (-item["files"], item["path"]))
    truncated = max(0, len(entries) - BRIEF_MAX_LAYERS)
    return entries[:BRIEF_MAX_LAYERS], truncated, role_by_file


# ---------------------------------------------------------------------------
# Construção e renderização
# ---------------------------------------------------------------------------


def build_brief(manifest, graph: Optional[dict], workspace_root: Path) -> dict:
    """
    Constrói o briefing completo a partir do manifest e (opcionalmente) do grafo.

    Sem grafo, degrada: sem símbolos, sem graus, sem hubs — mas identity, layers e
    entrypoints declarados continuam corretos.
    """
    warnings: List[str] = []
    workspace_root = Path(workspace_root)
    manifest_files = set(manifest.files.keys())

    per_file = _per_file_stats_from_graph(graph)
    identity = _compute_identity(manifest, graph, warnings)
    layers, layers_truncated, role_by_file = _compute_layers(manifest, per_file, warnings)

    import_edges = _import_edge_count(graph)
    if graph is not None and import_edges == 0:
        warnings.append("no_import_edges")
    if graph is None:
        warnings.append("graph_unavailable")

    entrypoints = _compute_entrypoints(
        workspace_root,
        manifest_files,
        graph,
        [layer["path"] for layer in layers],
        role_by_file,
    )

    brief = {
        "brief_version": BRIEF_SCHEMA_VERSION,
        "generated_at": manifest.last_indexed_at,
        "index_version": manifest.index_version,
        "git_head_sha": manifest.git_head_sha,
        "workspace_repo": identity["repo"],
        "source": {
            "graph_available": graph is not None,
            "import_edges": import_edges,
            "manifest_files": len(manifest_files),
            "total_chunks": manifest.total_chunks,
            "symbol_count": identity.get("symbols", 0),
        },
        "identity": identity,
        "layers": layers,
        "layers_truncated": layers_truncated,
        "entrypoints": entrypoints,
        "hubs": _compute_hubs(graph),
        "warnings": sorted(set(warnings)),
    }
    if graph is None:
        brief["degraded"] = True
    return brief


def build_and_write_brief(
    manifest,
    index_path: Path,
    workspace_root: Path,
    graph: Optional[dict] = None,
    return_metadata: bool = False,
):
    brief = build_brief(manifest, graph, workspace_root)
    brief_path, metadata = _persist_brief(brief, Path(index_path))
    if return_metadata:
        return brief_path, metadata
    return brief_path


def _level0_projection(brief: dict) -> dict:
    identity = brief["identity"]
    reduced_identity = {
        key: identity[key]
        for key in ("repo", "files", "chunks", "primary_language", "doc_ratio")
        if key in identity
    }
    if "symbols" in identity:
        reduced_identity["symbols"] = identity["symbols"]
    reduced_identity["languages"] = identity["languages"][:2]

    return {
        "identity": reduced_identity,
        "layers": [
            {"path": layer["path"], "role": layer["role"], "files": layer["files"]}
            for layer in brief["layers"]
        ],
        "entrypoints": [item["file_path"] for item in brief["entrypoints"]],
        "hubs": [hub["label"] for hub in brief["hubs"][:3]],
    }


def _next_steps(level: int, brief: dict) -> List[str]:
    if level == 0:
        return ["atlas_brief(level=1) para camadas, entrypoints e hubs detalhados"]

    steps = []
    source_layer = next(
        (layer["path"] for layer in brief["layers"] if layer["role"] == "source"),
        None,
    )
    if source_layer:
        steps.append(
            f'atlas_search(query="...", path_prefix="{source_layer}") para localizar uma implementação'
        )
    else:
        steps.append('atlas_search(query="...") para localizar uma implementação específica')
    if brief["hubs"]:
        steps.append(
            f'atlas_graph(mode="explain", target="{brief["hubs"][0]["id"]}") para a vizinhança de um hub'
        )
    if "graph_unavailable" in brief.get("warnings", []):
        steps.append("atlas_index() para gerar graph.json e habilitar hubs e ranking por conectividade")
    return steps


def _serialized_len(payload: dict) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def render_brief(
    brief: dict,
    level: int,
    *,
    current_git_sha: Optional[str] = None,
    computed_at_query_time: bool = False,
) -> dict:
    """
    Projeta o brief no nível pedido e garante o teto de caracteres como PÓS-CONDIÇÃO.

    `is_stale` nunca é lido do arquivo: é calculado aqui, comparando o SHA gravado com o
    HEAD atual passado pelo chamador.
    """
    warnings = list(brief.get("warnings", []))
    is_stale = bool(current_git_sha and brief.get("git_head_sha") and current_git_sha != brief["git_head_sha"])
    if is_stale:
        warnings.append("index_stale")
    if computed_at_query_time:
        warnings.append("brief_recomputed_at_query_time")

    payload: Dict[str, Any] = {
        "brief_version": brief["brief_version"],
        "level": level,
        "generated_at": brief.get("generated_at"),
        "git_head_sha": brief.get("git_head_sha"),
        "is_stale": is_stale,
    }

    if level == 0:
        payload.update(_level0_projection(brief))
        max_chars = BRIEF_LEVEL0_MAX_CHARS
    else:
        payload["index_version"] = brief.get("index_version")
        payload["current_git_head_sha"] = current_git_sha
        payload["computed_at_query_time"] = computed_at_query_time
        if is_stale:
            payload["stale_reason"] = "git_head_changed"
        if brief.get("degraded"):
            payload["degraded"] = True
        payload["source"] = brief.get("source", {})
        payload["identity"] = brief["identity"]
        payload["layers"] = [dict(layer) for layer in brief["layers"]]
        payload["layers_truncated"] = brief.get("layers_truncated", 0)
        payload["entrypoints"] = brief["entrypoints"]
        payload["hubs"] = brief["hubs"]
        max_chars = BRIEF_LEVEL1_MAX_CHARS

    payload["warnings"] = sorted(set(warnings))
    payload["next"] = _next_steps(level, brief)

    _enforce_budget(payload, max_chars)
    return payload


def _enforce_budget(payload: Dict[str, Any], max_chars: int) -> None:
    """
    Descarta conteúdo em ordem fixa até caber no orçamento, sinalizando o corte.
    Sem isso o teto seria estimativa; com isso é invariante.
    """
    if _serialized_len(payload) <= max_chars:
        return

    def _mark() -> None:
        warnings = set(payload.get("warnings", []))
        warnings.add("truncated_for_budget")
        payload["warnings"] = sorted(warnings)

    # 1. cauda de layers[].top
    for layer in payload.get("layers", []):
        while isinstance(layer, dict) and layer.get("top"):
            layer["top"].pop()
            _mark()
            if _serialized_len(payload) <= max_chars:
                return

    # 2. cauda de hubs
    while payload.get("hubs"):
        payload["hubs"].pop()
        _mark()
        if _serialized_len(payload) <= max_chars:
            return

    # 3. cauda de layers
    while len(payload.get("layers", [])) > 1:
        payload["layers"].pop()
        payload["layers_truncated"] = payload.get("layers_truncated", 0) + 1
        _mark()
        if _serialized_len(payload) <= max_chars:
            return


def build_brief_lazily(manifest, index_dir: Path, workspace_root: Path) -> dict:
    """
    Recomputa o brief sob demanda quando `brief.json` não existe (índice de versão
    anterior) e tenta persistir. A persistência é best-effort: o diretório pode estar
    somente-leitura ou em meio a uma reindexação.
    """
    graph = None
    if (Path(index_dir) / GRAPH_FILENAME).exists():
        try:
            graph = load_graph(Path(index_dir))
        except Exception:
            graph = None

    brief = build_brief(manifest, graph, workspace_root)
    try:
        _persist_brief(brief, Path(index_dir))
    except Exception as e:
        print(f"[atlas] Falha ao persistir brief.json sob demanda: {e}", file=sys.stderr)
    return brief
