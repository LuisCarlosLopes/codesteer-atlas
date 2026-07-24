import sys
import os
import argparse
import json
import posixpath
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Optional
from urllib.parse import unquote, urlparse

# 1. Duplica o file descriptor real do stdout (fd 1) para uso exclusivo do
# transporte stdio do MCP, e redireciona fd 1 (em nível de SO) para fd 2 (stderr).
# Isso é necessário porque bibliotecas nativas usadas durante o embedding/indexação
# (onnxruntime, lancedb/tantivy, etc.) podem escrever diretamente no fd 1, e essas
# escritas correm em paralelo (thread de reindex em background) com as respostas
# JSON-RPC que o FastMCP escreve no mesmo fd, corrompendo o protocolo stdio.
_real_stdout_fd = os.dup(1)
os.dup2(2, 1)
# encoding/newline explícitos: no Windows o default seria cp1252 + tradução \r\n,
# o que corromperia o JSON-RPC se o SDK escrever direto em sys.stdout
original_stdout = os.fdopen(_real_stdout_fd, "w", encoding="utf-8", newline="\n", closefd=True)

# 2. Redireciona sys.stdout para stderr imediatamente para evitar que
# qualquer import de dependência pesada (como lancedb, torch, etc.) polua o stdout
sys.stdout = sys.stderr

# 3. Suprime logging nativo de bibliotecas Rust (lancedb/tantivy) que pode ser
# escrito diretamente no fd 1/stdout do processo, contornando o redirecionamento
# Python acima em alguns ambientes (ex: Windows, onde DLLs com CRT próprio podem
# obter o handle original de STDOUT via GetStdHandle, ignorando os.dup2).
os.environ.setdefault("RUST_LOG", "off")
os.environ.setdefault("LANCE_LOG", "off")

# Agora realizamos os imports de forma segura
import anyio  # noqa: E402
from fastmcp import Context, FastMCP  # noqa: E402
from mcp.shared.session import RequestResponder  # noqa: E402
from codesteer_atlas.config import (  # noqa: E402
    BACKGROUND_REINDEX_MIN_INTERVAL_S,
    DEFAULT_INDEX_DIR,
    GRAPH_FILENAME,
    GRAPH_HTML_FILENAME,
    GRAPH_PATH_MAX_HOPS,
    SUPPORTED_EXTENSIONS,
)
from codesteer_atlas.embeddings import EmbeddingEngine, FASTEMBED_MODEL_NAME  # noqa: E402
from codesteer_atlas.graph import bfs_path, explain, hubs, load_graph  # noqa: E402
from codesteer_atlas.locking import is_reindex_locked  # noqa: E402
from codesteer_atlas.markdown_links import (  # noqa: E402
    extract_markdown_link_targets,
    slugify_heading,
)
from codesteer_atlas.rationale import deserialize_rationale_ref  # noqa: E402
from codesteer_atlas.storage import StorageBackend  # noqa: E402
from codesteer_atlas.indexer import (  # noqa: E402
    get_git_head_sha,
    index_workspace,
    load_atlasignore_spec,
    should_ignore,
)

# 4. Patch defensivo no SDK do MCP: se um tool handler síncrono demorar o
# suficiente para o cliente desistir (timeout) e enviar `notifications/cancelled`,
# o `RequestResponder` é marcado como `_completed=True`. Quando o handler
# finalmente termina e tenta responder, `respond()` levanta
# `AssertionError: Request already responded to`, que não é capturada em
# nenhum lugar do TaskGroup do anyio e derruba o processo inteiro (encerrando a
# conexão MCP). Como nosso handler síncrono não pode observar o cancelamento
# no meio da execução, simplesmente ignoramos a resposta tardia em vez de
# travar o servidor inteiro [GA-XX].
_original_responder_respond = RequestResponder.respond


async def _safe_responder_respond(self, response):
    if self._completed:
        print(
            f"[atlas] Ignorando resposta tardia para request {self.request_id} "
            "(já cancelada/concluída pelo cliente, provável timeout).",
            file=sys.stderr,
        )
        return
    await _original_responder_respond(self, response)


RequestResponder.respond = _safe_responder_respond

# Inicializa o servidor FastMCP
app = FastMCP("CodeSteer Atlas")

# Nome do diretório padrão do índice (usado tanto para discovery quanto para
# resolução do workspace pai do índice)
INDEX_DIR_NAME = ".code-index"

# Origem da última resolução do índice (cli-arg | env | discovery |
# editor-project-dir | roots | roots-fallback | editor-project-dir-fallback |
# cwd-fallback), exposta em `atlas_status` para autodiagnóstico de configuração
# do cliente MCP (ex.: Windows GUI lança o servidor com CWD arbitrário e a
# discovery falha)
INDEX_RESOLUTION_SOURCE = "cwd-fallback"

# Timeout (s) do request `roots/list` ao cliente, para não travar um tool sync
# caso o cliente declare a capability `roots` mas não responda [R].
ROOTS_LIST_TIMEOUT_S = 5.0

# A resolução via MCP roots é tentada no máximo uma vez por processo: o root do
# workspace é estável durante toda a sessão do editor. O lock serializa a
# primeira resolução entre threads (FastMCP roda tools sync em threadpool) [R].
_ROOTS_RESOLUTION_DONE = False
_ROOTS_RESOLUTION_LOCK = threading.Lock()


def _recommend_dry_run_paths(candidates: list[dict], total_eligible: int) -> tuple[str, list[str], str]:
    ranked = sorted(
        (candidate for candidate in candidates if "/" not in candidate["path"]),
        key=lambda item: (-item["eligible_files"], item["path"]),
    )
    preferred_names = {"src", "docs", "app", "apps", "packages", "backend", "frontend"}
    recommended = [
        candidate["path"]
        for candidate in ranked
        if candidate["path"] in preferred_names or candidate["eligible_files"] > 1
    ][:3]
    if total_eligible > 200 and recommended:
        return (
            "paths",
            recommended,
            "Workspace grande detectado; prefira indexação parcial primeiro.",
        )
    return ("workspace", [], "Workspace pequeno/médio; indexação completa é razoável.")


def _discover_index_dir(base: Path) -> Optional[Path]:
    """Sobe a partir de `base` procurando uma pasta `.code-index` (estilo `.git`)."""
    base = base.resolve()
    for candidate in [base, *base.parents]:
        candidate_index = candidate / INDEX_DIR_NAME
        if candidate_index.exists():
            return candidate_index
    return None


def _file_uri_to_path(uri: str) -> Optional[Path]:
    """
    Converte uma URI `file://` (informada por um MCP root) em `Path`, de forma
    cross-platform. Retorna `None` para esquemas não-`file` ou path vazio.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = unquote(parsed.path)
    # Windows: `/C:/Users/...` -> `C:/Users/...`
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path) if path else None


def _list_roots_sync(ctx: "Context") -> list[Path]:
    """
    Obtém os workspace roots informados pelo cliente MCP (capability `roots`) a
    partir de um tool handler síncrono. A ponte sync->async usa
    `anyio.from_thread.run`, válida porque o FastMCP executa tools síncronos em
    worker thread via `anyio.to_thread.run_sync`. Best-effort: qualquer falha
    (cliente sem suporte a `roots`, timeout, transporte) retorna lista vazia [R].
    """
    if ctx is None:
        return []

    async def _call() -> list:
        with anyio.fail_after(ROOTS_LIST_TIMEOUT_S):
            return await ctx.list_roots()

    try:
        roots = anyio.from_thread.run(_call)
    except Exception as e:
        print(
            f"[atlas] roots/list indisponível ({type(e).__name__}); ignorando.",
            file=sys.stderr,
        )
        return []

    paths: list[Path] = []
    for root in roots or []:
        uri = getattr(root, "uri", None)
        if uri is None:
            continue
        resolved = _file_uri_to_path(str(uri))
        if resolved is not None:
            paths.append(resolved)
    return paths


def _resolve_index_dir_via_roots(ctx: "Context") -> None:
    """
    Atualiza `INDEX_DIR_PATH`/`INDEX_RESOLUTION_SOURCE` usando os workspace roots
    do cliente MCP, quando a resolução de startup caiu em fallback (nenhum índice
    localizado por `--index-dir`, `ATLAS_INDEX_DIR`, discovery do CWD ou env do
    editor).

    Cobre o caso de um servidor MCP registrado globalmente como plugin (Copilot,
    Cursor, Kiro), iniciado com CWD = HOME e sem as env vars próprias do editor:
    os `roots` informam a raiz real do projeto e o índice é resolvido lá — sem
    exigir configuração local por projeto [R].

    Executa no máximo uma vez por processo (root estável na sessão do editor).
    Sem efeito quando `ctx` é `None` (chamada direta em testes unitários).
    """
    global INDEX_DIR_PATH, INDEX_RESOLUTION_SOURCE, _ROOTS_RESOLUTION_DONE

    if ctx is None or _ROOTS_RESOLUTION_DONE:
        return

    with _ROOTS_RESOLUTION_LOCK:
        if _ROOTS_RESOLUTION_DONE:
            return

        # Fontes de alta confiança / índice já localizado: não sobrescreve.
        if INDEX_RESOLUTION_SOURCE in ("cli-arg", "env", "discovery", "editor-project-dir"):
            _ROOTS_RESOLUTION_DONE = True
            return

        roots = _list_roots_sync(ctx)
        if not roots:
            _ROOTS_RESOLUTION_DONE = True
            return

        # 1) Procura um `.code-index` existente subindo a partir de cada root.
        for root in roots:
            found = _discover_index_dir(root)
            if found is not None:
                INDEX_DIR_PATH = found
                INDEX_RESOLUTION_SOURCE = "roots"
                _ROOTS_RESOLUTION_DONE = True
                print(f"[atlas] Índice resolvido via MCP roots: {found}", file=sys.stderr)
                return

        # 2) Nenhum índice ainda: aponta para `.code-index` na raiz do 1º root,
        #    para que uma futura `atlas_index` crie o índice no projeto (e não no HOME).
        target = roots[0].resolve() / INDEX_DIR_NAME
        INDEX_DIR_PATH = target
        INDEX_RESOLUTION_SOURCE = "roots-fallback"
        _ROOTS_RESOLUTION_DONE = True
        print(
            f"[atlas] Índice (inexistente) apontado via MCP roots para: {target}",
            file=sys.stderr,
        )


def resolve_index_dir(
    cli_arg: Optional[str] = None,
    env: Optional[dict] = None,
    start_dir: Optional[Path] = None,
) -> Path:
    """
    Resolve o diretório do índice seguindo a cadeia DECISAO-002:
    1. `--index-dir` (argumento de linha de comando) — maior prioridade
    2. `ATLAS_INDEX_DIR` (variável de ambiente)
    3. Discovery ascendente: sobe a partir de `start_dir` (default: CWD)
       procurando uma pasta `.code-index` (estilo `.git`), até a raiz do filesystem.
    4. Discovery ascendente a partir da raiz do projeto/workspace informada pelo
       editor via variável de ambiente — `CLAUDE_PROJECT_DIR` (Claude Code) ou
       `WORKSPACE_FOLDER_PATHS` (Cursor/VS Code) — usada quando o servidor MCP
       de um plugin é iniciado com outro CWD (ex.: o HOME do usuário) e o passo
       3 não encontra o `.code-index` do projeto.

    Se nenhum mecanismo resolver, retorna `.code-index` relativo à raiz do
    projeto informada pelo editor (quando disponível) ou ao CWD (o caller
    decide como reportar a ausência do índice).

    Como efeito colateral, registra a origem usada em `INDEX_RESOLUTION_SOURCE`.
    """
    global INDEX_RESOLUTION_SOURCE

    if cli_arg:
        resolved = Path(cli_arg)
        INDEX_RESOLUTION_SOURCE = "cli-arg"
        print(f"[atlas] Índice resolvido via --index-dir: {resolved}", file=sys.stderr)
        return resolved

    env = env if env is not None else os.environ
    env_value = env.get("ATLAS_INDEX_DIR")
    if env_value:
        resolved = Path(env_value)
        INDEX_RESOLUTION_SOURCE = "env"
        print(f"[atlas] Índice resolvido via ATLAS_INDEX_DIR: {resolved}", file=sys.stderr)
        return resolved

    # Discovery ascendente a partir do CWD (ou start_dir informado)
    current = (start_dir or Path.cwd()).resolve()
    found = _discover_index_dir(current)
    if found is not None:
        INDEX_RESOLUTION_SOURCE = "discovery"
        print(
            f"[atlas] Índice resolvido via discovery ascendente: {found}",
            file=sys.stderr,
        )
        return found

    # Fallback: discovery ascendente a partir da raiz do projeto informada
    # pelo editor. Plugins MCP podem ser iniciados com CWD diferente da raiz
    # do projeto (ex.: HOME do usuário), então o passo acima não encontra o
    # `.code-index` do projeto. Editores expõem a raiz real do projeto via
    # variáveis de ambiente próprias:
    # - `CLAUDE_PROJECT_DIR`: Claude Code.
    # - `WORKSPACE_FOLDER_PATHS`: Cursor/VS Code (pode conter múltiplos paths
    #   separados por `os.pathsep`; usamos o primeiro).
    project_dir_value = env.get("CLAUDE_PROJECT_DIR")
    project_dir_source = "CLAUDE_PROJECT_DIR"
    if not project_dir_value:
        workspace_paths = env.get("WORKSPACE_FOLDER_PATHS")
        if workspace_paths:
            project_dir_value = workspace_paths.split(os.pathsep)[0]
            project_dir_source = "WORKSPACE_FOLDER_PATHS"

    if project_dir_value:
        project_dir = Path(project_dir_value)
        found = _discover_index_dir(project_dir)
        if found is not None:
            INDEX_RESOLUTION_SOURCE = "editor-project-dir"
            print(
                f"[atlas] Índice resolvido via {project_dir_source}: {found}",
                file=sys.stderr,
            )
            return found

        INDEX_RESOLUTION_SOURCE = "editor-project-dir-fallback"
        fallback = project_dir.resolve() / INDEX_DIR_NAME
        print(
            "[atlas] AVISO: não foi possível resolver o diretório do índice "
            f"(--index-dir, ATLAS_INDEX_DIR e discovery a partir de {current} "
            f"e de {project_dir_source}={project_dir} falharam). "
            f"Usando o padrão relativo a {project_dir_source}: {fallback}",
            file=sys.stderr,
        )
        return fallback

    INDEX_RESOLUTION_SOURCE = "cwd-fallback"
    print(
        "[atlas] AVISO: não foi possível resolver o diretório do índice "
        f"(--index-dir, ATLAS_INDEX_DIR e discovery a partir de {current} falharam). "
        f"Usando o padrão: {DEFAULT_INDEX_DIR}",
        file=sys.stderr,
    )
    return DEFAULT_INDEX_DIR


def _allowed_workspace_roots(ctx: "Context | None" = None) -> list[Path]:
    """
    Raízes de filesystem permitidas para o parâmetro `workspace` da tool
    `atlas_index` (anti-leitura arbitrária via MCP).
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(root: Path) -> None:
        resolved = root.resolve()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            roots.append(resolved)

    index_root = _index_workspace_root()
    if index_root.exists():
        _add(index_root)

    for root in _list_roots_sync(ctx):
        _add(root)

    env = os.environ
    project_dir_value = env.get("CLAUDE_PROJECT_DIR")
    if not project_dir_value:
        workspace_paths = env.get("WORKSPACE_FOLDER_PATHS")
        if workspace_paths:
            project_dir_value = workspace_paths.split(os.pathsep)[0]
    if project_dir_value:
        project_dir = Path(project_dir_value)
        if project_dir.exists():
            _add(project_dir)

    return roots


def _validate_workspace_allowed(workspace_path: Path, ctx: "Context | None" = None) -> None:
    """Levanta ValueError se `workspace_path` estiver fora das raízes permitidas."""
    allowed_roots = _allowed_workspace_roots(ctx)
    if not allowed_roots:
        return

    resolved = workspace_path.resolve()
    for root in allowed_roots:
        if resolved == root or resolved.is_relative_to(root):
            return

    allowed_display = ", ".join(str(root) for root in allowed_roots)
    raise ValueError(
        f"O workspace '{resolved}' está fora das raízes permitidas: {allowed_display}."
    )


def _index_not_found_error(storage: "StorageBackend") -> FileNotFoundError:
    """Mensagem de erro acionável listando os 3 mecanismos de resolução do índice."""
    return FileNotFoundError(
        f"Índice local não encontrado em '{storage.index_dir.resolve()}'. "
        "O diretório do índice é resolvido na seguinte ordem: "
        "(1) argumento --index-dir do servidor, "
        "(2) variável de ambiente ATLAS_INDEX_DIR, "
        "(3) busca ascendente por uma pasta '.code-index' a partir do CWD, "
        "(4) raiz do projeto informada pelo editor (CLAUDE_PROJECT_DIR / "
        "WORKSPACE_FOLDER_PATHS), "
        "(5) workspace roots informados pelo cliente MCP (capability 'roots'). "
        "Execute a indexação primeiro: 'atlas-index --workspace <caminho>' "
        "ou use a tool 'atlas_index'."
    )


# Caminho global do índice. Pode ser sobrescrito por argumento de linha de comando
# (ver main()) ou variável de ambiente ATLAS_INDEX_DIR; caso contrário, é
# resolvido por discovery ascendente a partir do CWD.
INDEX_DIR_PATH = resolve_index_dir()


def _index_workspace_root() -> Path:
    """
    Retorna o diretório de workspace correspondente ao índice resolvido
    (o pai de `.code-index`), usado como base para staleness e como default
    de `workspace` na tool `atlas_index` [C].
    """
    resolved = INDEX_DIR_PATH.resolve()
    if resolved.name == INDEX_DIR_NAME:
        return resolved.parent
    return resolved.parent if resolved.parent != resolved else resolved


def get_status_data() -> dict:
    """Função auxiliar para obter os metadados e status de diagnóstico do índice."""
    storage = StorageBackend(index_dir=INDEX_DIR_PATH)
    reindexing = is_reindex_locked(INDEX_DIR_PATH)
    graph_path = storage.index_dir / GRAPH_FILENAME
    graph_viewer_path = storage.index_dir / GRAPH_HTML_FILENAME

    if not storage.exists():
        return {
            "index_exists": False,
            "total_chunks": 0,
            "repos_indexed": [],
            "embedding_model": FASTEMBED_MODEL_NAME,
            "embedding_backend": "fastembed",
            "storage_backend": "lancedb",
            "index_path": str(storage.index_dir.resolve()),
            "index_resolution": INDEX_RESOLUTION_SOURCE,
            "last_indexed_at": None,
            "git_head_sha": None,
            "is_stale": False,
            "languages_indexed": [],
            "reindexing": reindexing,
            "graph_available": False,
            "graph_viewer_path": None,
        }

    try:
        manifest = storage.get_manifest()

        # Staleness é calculada a partir do workspace pai do índice resolvido [C],
        # não do CWD do processo (que pode ser arbitrário em clientes MCP).
        current_git_sha = get_git_head_sha(_index_workspace_root())
        is_stale = False
        if current_git_sha and manifest.git_head_sha:
            is_stale = current_git_sha != manifest.git_head_sha

        return {
            "index_exists": True,
            "total_chunks": manifest.total_chunks,
            "repos_indexed": manifest.repos_indexed,
            "embedding_model": manifest.embedding_model,
            "embedding_backend": manifest.embedding_backend,
            "storage_backend": manifest.storage_backend,
            "index_path": str(storage.index_dir.resolve()),
            "index_resolution": INDEX_RESOLUTION_SOURCE,
            "last_indexed_at": manifest.last_indexed_at,
            "git_head_sha": manifest.git_head_sha,
            "is_stale": is_stale,
            "languages_indexed": manifest.languages_indexed,
            "reindexing": reindexing,
            "graph_available": graph_path.exists(),
            "graph_viewer_path": str(graph_viewer_path.resolve()) if graph_viewer_path.exists() else None,
        }
    except Exception as e:
        print(f"Erro ao ler diagnóstico do índice: {e}", file=sys.stderr)
        return {
            "index_exists": True,
            "error": str(e),
            "index_resolution": INDEX_RESOLUTION_SOURCE,
            "reindexing": reindexing,
            "graph_available": False,
            "graph_viewer_path": None,
        }


def _resolve_note_candidates(name_to_paths: dict, key: str) -> list[str]:
    if key in name_to_paths:
        return sorted(name_to_paths[key])
    prefix = f"{key}-"
    matches = []
    for stem, paths in name_to_paths.items():
        if stem.startswith(prefix):
            matches.extend(paths)
    return sorted(matches)


# --- MCP Tools ---


@app.tool()
def atlas_search(
    query: str,
    top_k: int = 5,
    repo: Optional[str] = None,
    language: Optional[str] = None,
    path_prefix: Optional[str] = None,
    include_content: bool = False,
    limit: Optional[int] = None,
    ctx: "Context | None" = None,
) -> str:
    """
    Search code AND documents in the project's local index — your FIRST tool to find,
    explore or investigate anything here, before broad file reads or grep.

    Runs a semantic hybrid search (vector + BM25, fused via RRF) over pre-indexed chunks
    of source code (classes/functions/methods) and documents (Markdown, text, JSON/YAML/
    TOML). Pass natural language or exact symbols. For a structural overview instead, use
    `atlas_map`.

    Token-efficient two-pass pattern: by default this returns metadata only (file_path,
    lines, symbol, type, score). Locate first, then read the exact lines with `Read`, or
    re-call with include_content=true for the few results whose content you actually need.

    Call directly — do NOT call `atlas_status` first "just to check". If the index does
    not exist yet, this raises an actionable error explaining how to build it (see
    `atlas_index`).

    Args:
        query: Natural language description or exact symbols to find.
        top_k: Max results, 1-50. Defaults to 5.
        repo: Optional repository name filter.
        language: Optional language filter (e.g. 'python', 'javascript', 'go').
        path_prefix: Optional path prefix filter (e.g. 'src/controllers').
        include_content: When true, includes each result's 'content'. Defaults to false
            (metadata/location only) to save tokens.
        limit: Alias for 'top_k'; overrides it when provided.

    Returns:
        JSON with `results` (each: file_path, lines, symbol, type, language, score, repo,
        and `content` only when include_content=true), `total_chunks_searched`, and
        `query_time_ms`. Markdown results may also include `markdown_references`
        ({file_path, anchor, resolved_section}) for links to other `.md` files.
        Code results may include `rationale_refs` ({kind, key, note_path?, text?, candidates?}).
    """
    start_time = time.time()

    if limit is not None:
        top_k = limit

    # Validação obrigatória da entrada [V01]
    if not query or not query.strip():
        raise ValueError("O parâmetro 'query' é obrigatório e não pode ser vazio.")

    # Validação do limite de resultados [V02, L01]
    if top_k < 1 or top_k > 50:
        raise ValueError("O parâmetro 'top_k' deve estar entre 1 e 50.")

    _resolve_index_dir_via_roots(ctx)
    storage = StorageBackend(index_dir=INDEX_DIR_PATH)
    if not storage.exists():
        raise _index_not_found_error(storage)

    # Coleta filtros informados
    filters = {}
    if repo:
        filters["repo"] = repo
    if language:
        filters["language"] = language
    if path_prefix:
        filters["path_prefix"] = path_prefix

    # Inicializa o EmbeddingEngine e gera o embedding da query (Lazy Loading) [GA-07]
    embedding_engine = EmbeddingEngine()
    query_vector = embedding_engine.encode_single(query)

    # Executa a busca híbrida (cosseno + BM25 FTS + RRF) no LanceDB
    results = storage.search_hybrid(
        query_vector=query_vector, query_text=query, filters=filters, top_k=top_k
    )

    query_time_ms = (time.time() - start_time) * 1000
    manifest = storage.get_manifest()

    # Cache local de seções por arquivo referenciado, para evitar lookups
    # repetidos ao resolver #anchor de múltiplos links para o mesmo .md [F]
    sections_cache: dict = {}

    # Mapa stem (lowercase, sem .md) -> paths .md, para resolver wikilinks
    # "bare" ([[mcp-server]]) globalmente contra manifest.files [F]
    name_to_paths: dict = {}
    for file_path in manifest.files:
        if file_path.lower().endswith(".md"):
            stem = posixpath.basename(file_path)[: -len(".md")].lower()
            name_to_paths.setdefault(stem, []).append(file_path)

    serialized_results = []
    for r in results:
        item = {
            "file_path": r.file_path,
            "lines": [r.start_line, r.end_line],
            "symbol": r.scope_name,
            "type": r.scope_type,
            "language": r.language,
            "score": r.score,
            "repo": r.repo,
        }
        if include_content:
            item["content"] = r.content

        # Enriquece resultados markdown com referências a outros .md detectadas
        # no conteúdo, resolvendo #anchor contra seções já indexadas (Seção 4.2 do plan.md)
        if r.language == "markdown":
            targets = extract_markdown_link_targets(
                r.content, r.file_path, name_to_paths=name_to_paths
            )
            if targets:
                markdown_references = []
                for target in targets:
                    resolved_section = None
                    if target.anchor is not None and target.file_path is not None:
                        if target.file_path not in sections_cache:
                            sections_cache[target.file_path] = storage.get_sections_by_file_path(
                                target.file_path
                            )
                        target_slug = slugify_heading(target.anchor)
                        for section in sections_cache[target.file_path]:
                            if slugify_heading(section["scope_name"]) == target_slug:
                                resolved_section = section["scope_name"]
                                break
                    ref = {
                        "file_path": target.file_path,
                        "anchor": target.anchor,
                        "resolved_section": resolved_section,
                    }
                    if target.alias is not None:
                        ref["alias"] = target.alias
                    if target.candidates:
                        ref["candidates"] = target.candidates
                    markdown_references.append(ref)
                item["markdown_references"] = markdown_references
        elif r.references:
            rationale_refs = []
            for raw_ref in r.references:
                ref = deserialize_rationale_ref(raw_ref)
                if ref is None:
                    continue
                if ref.kind == "annotation":
                    rationale_refs.append(
                        {"kind": "annotation", "key": ref.key, "text": ref.text}
                    )
                    continue
                candidates = _resolve_note_candidates(name_to_paths, ref.key)
                resolved = candidates[0] if len(candidates) == 1 else None
                entry = {
                    "kind": ref.kind,
                    "key": ref.key,
                    "note_path": resolved,
                }
                if len(candidates) > 1:
                    entry["candidates"] = candidates
                rationale_refs.append(entry)
            if rationale_refs:
                item["rationale_refs"] = rationale_refs

        serialized_results.append(item)

    response = {
        "results": serialized_results,
        "total_chunks_searched": manifest.total_chunks,
        "query_time_ms": round(query_time_ms, 2),
    }

    return json.dumps(response, separators=(",", ":"), ensure_ascii=False)


@app.tool()
def atlas_graph(
    mode: str,
    target: Optional[str] = None,
    source: Optional[str] = None,
    top_n: int = 10,
    ctx: "Context | None" = None,
) -> str:
    """
    Query the derived knowledge graph for hubs, paths, or neighborhood explanations.

    Call this directly when the question is about connectivity, rationale, or
    centrality in the indexed workspace. It reads the derived `graph.json`
    produced by `atlas_index`; it does not rebuild the graph itself.

    Args:
        mode: One of `hubs`, `path`, or `explain`.
        target: Required for `path` and `explain`. Accepts exact node id, exact label,
            or a unique suffix.
        source: Required for `path`. Accepts exact node id, exact label, or a unique suffix.
        top_n: Number of hubs to return for `hubs` mode. Must be between 1 and 50.

    Returns:
        JSON string for the selected mode.
    """
    _resolve_index_dir_via_roots(ctx)

    if mode not in {"hubs", "path", "explain"}:
        raise ValueError("O parâmetro 'mode' deve ser 'hubs', 'path' ou 'explain'.")
    if top_n < 1 or top_n > 50:
        raise ValueError("O parâmetro 'top_n' deve estar entre 1 e 50.")

    graph = load_graph(INDEX_DIR_PATH)

    if mode == "hubs":
        payload = {"mode": "hubs", "items": hubs(graph, top_n)}
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    if mode == "path":
        if not source or not target:
            raise ValueError("Os parâmetros 'source' e 'target' são obrigatórios em mode='path'.")
        payload = bfs_path(graph, source, target, max_hops=GRAPH_PATH_MAX_HOPS)
        payload["mode"] = "path"
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    if not target:
        raise ValueError("O parâmetro 'target' é obrigatório em mode='explain'.")
    payload = explain(graph, target)
    payload["mode"] = "explain"
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


@app.tool()
def atlas_map(
    repo: Optional[str] = None,
    path_prefix: Optional[str] = None,
    max_depth: int = 3,
    query: Optional[str] = None,  # Aceito para compatibilidade com clientes MCP que injetam 'query'
    ctx: "Context | None" = None,
) -> str:
    """
    Retrieve a structured hierarchical tree map of classes, methods, and functions in the workspace.

    Provides a compact, token-efficient overview of the codebase's architecture and
    logical structure. Use this to understand how the project is organized, list
    classes/functions/methods within folders, or find entrypoints without retrieving
    full file contents. To find a specific implementation, concept, or document instead
    — or to explore/investigate any area of the project — use `atlas_search`.

    If the index does not exist yet, this raises an actionable error explaining how to
    build it (see `atlas_index`).

    Args:
        repo: Optional repository name to filter the map.
        path_prefix: Optional file path prefix to filter the map to a specific directory.
        max_depth: Maximum directory depth level in the hierarchical map. Defaults to 3.
        query: Optional search query (accepted for client compatibility and ignored).

    Returns:
        JSON string with `map` (indented text tree of files and their symbols),
        `total_files`, `total_symbols`, and `repo`.
    """
    _resolve_index_dir_via_roots(ctx)
    storage = StorageBackend(index_dir=INDEX_DIR_PATH)
    if not storage.exists():
        raise _index_not_found_error(storage)

    # Coleta apenas as colunas necessárias (sem vector, sem to_pandas) [F][M]
    chunks = storage.get_symbols()

    # Aplica os filtros informados (repo não está disponível em get_symbols;
    # path_prefix é aplicado sobre file_path)
    if path_prefix:
        prefix = PurePath(path_prefix).as_posix()
        chunks = [c for c in chunks if c["file_path"].startswith(prefix)]

    # Constrói a estrutura hierárquica
    # file_path -> list of symbols
    hierarchy: dict = {}
    total_symbols = 0
    unique_files = set()

    for chunk in chunks:
        file_path = chunk["file_path"]
        unique_files.add(file_path)

        # Filtra por profundidade do caminho
        path_parts = Path(file_path).parts
        if len(path_parts) > max_depth + 1:
            # Agrupa em nível de diretório limite
            parent_dir = "/".join(path_parts[:max_depth]) + "/..."
            if parent_dir not in hierarchy:
                hierarchy[parent_dir] = []
            continue

        if file_path not in hierarchy:
            hierarchy[file_path] = []

        # Não adiciona o símbolo genérico 'module' no mapa para economizar tokens
        if chunk["scope_type"] != "module":
            hierarchy[file_path].append({"name": chunk["scope_name"], "type": chunk["scope_type"]})
            total_symbols += 1

    # Formata a árvore de arquitetura textual de forma compacta, sem emojis [G]
    lines = []

    # Ordena caminhos de arquivo para consistência
    for file_path in sorted(hierarchy.keys()):
        symbols = hierarchy[file_path]
        lines.append(file_path)

        # Ordena símbolos por nome
        sorted_symbols = sorted(symbols, key=lambda s: s["name"])
        for sym in sorted_symbols:
            prefix_label = {"class": "class", "function": "func", "method": "method"}.get(
                sym["type"], sym["type"]
            )
            lines.append(f"  {prefix_label} {sym['name']}")

    response = {
        "map": "\n".join(lines),
        "total_files": len(unique_files),
        "total_symbols": total_symbols,
        "repo": repo if repo else "all",
    }

    return json.dumps(response, separators=(",", ":"), ensure_ascii=False)


@app.tool()
def atlas_status(ctx: "Context | None" = None) -> str:
    """
    Get diagnostic metadata and health status of the local vector index.

    Use this only for explicit diagnostics (e.g. the user asks about index health,
    staleness, or which repos/languages are indexed) or to decide whether `atlas_index`
    should be run. It is NOT a precondition for `atlas_search`/`atlas_map` — call those
    directly; they raise an actionable error themselves if the index is missing.
    Never indexes anything itself.

    Returns:
        JSON string with `index_exists`, `total_chunks`, `repos_indexed`,
        `languages_indexed`, `embedding_model`, `embedding_backend`, `storage_backend`,
        `index_path`, `index_resolution` (how the index directory was resolved:
        "cli-arg" | "env" | "discovery" | "editor-project-dir" | "roots" |
        "roots-fallback" | "editor-project-dir-fallback" | "cwd-fallback" — useful to
        diagnose client misconfiguration when `index_exists` is unexpectedly false),
        `last_indexed_at`,
        `git_head_sha`, `is_stale` (true when the indexed git HEAD differs from the
        workspace's current HEAD), and `reindexing` (true when another process
        currently holds the reindex lock for this index).
    """
    _resolve_index_dir_via_roots(ctx)
    data = get_status_data()
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


@app.tool()
def atlas_index(
    workspace: Optional[str] = None,
    paths: Optional[list[str]] = None,
    full: bool = False,
    dry_run: bool = False,
    ctx: "Context | None" = None,
) -> str:
    """
    Index (or re-index) source code and documents into the local search index.

    Use to build the index the first time, or to refresh it after `atlas_status`
    reports `is_stale: true` or large changes.

    IMPORTANT: unless the user already said what to index, call with dry_run=true
    first, show the candidate folders, and ASK whether to index everything or
    specific folders. Then call again with the chosen 'paths' (or none for the
    whole workspace).

    Incremental by default: unchanged files (by content hash) are skipped, so
    re-runs are fast. full=true forces a complete rebuild.

    full=true or empty/omitted 'paths' (whole-workspace) run asynchronously in a
    background subprocess and return immediately — poll `atlas_status`
    (`reindexing: true` while running). A non-empty 'paths' with full=false runs
    synchronously and returns stats directly.

    Args:
        workspace: Absolute path to index. Defaults to the parent of the resolved
            index, else the current directory. Must exist and be a directory.
        paths: Optional subfolders (relative to 'workspace') to index, e.g.
            ["src", "docs"]. Omitted = whole workspace. No path traversal outside it.
        full: Force full re-index ignoring cached hashes. Defaults to false.
        dry_run: When true, indexes nothing; returns top-level candidate folders
            with eligible-file counts so you can present them before deciding.
            Defaults to false.

    Returns:
        dry_run=true: JSON with `workspace`, `candidates` ({path, eligible_files}),
        `total_eligible_files`.
        async (full=true or empty 'paths'): JSON with `workspace`, `indexed_paths`,
        `status` ("started"|"skipped"|"error"), `log_path`, optional
        `pid`/`reason`/`error`, and `message`.
        sync ('paths' set, full=false): JSON with `workspace`, `indexed_paths`,
        `files_processed`, `files_skipped_unchanged`, `files_removed`,
        `chunks_persisted`, `duration_s`, `git_head_sha`, and optional
        `skipped_reason`/`message` if another process holds the reindex lock.
    """
    # Resolve o índice via MCP roots quando a resolução de startup caiu em fallback,
    # antes de derivar o workspace do pai do índice [R].
    _resolve_index_dir_via_roots(ctx)

    # Resolve o workspace: parâmetro -> pai do índice resolvido -> CWD
    if workspace:
        workspace_path = Path(workspace).resolve()
    else:
        workspace_path = _index_workspace_root().resolve()
        if not workspace_path.exists():
            workspace_path = Path.cwd().resolve()

    if not workspace_path.exists() or not workspace_path.is_dir():
        raise ValueError(f"O diretório do workspace '{workspace_path}' não existe.")

    _validate_workspace_allowed(workspace_path, ctx)

    # Validação anti-traversal dos paths informados
    if paths:
        for raw_path in paths:
            candidate = Path(raw_path)
            resolved = candidate if candidate.is_absolute() else workspace_path / candidate
            resolved = resolved.resolve()
            if not resolved.is_relative_to(workspace_path):
                raise ValueError(
                    f"O path '{raw_path}' está fora do workspace '{workspace_path}' "
                    "(path traversal não é permitido)."
                )

    if dry_run:
        candidates = []
        total_eligible = 0
        atlas_spec = load_atlasignore_spec(workspace_path)

        try:
            top_level_entries = sorted(workspace_path.iterdir())
        except Exception as e:
            raise ValueError(f"Não foi possível listar o workspace '{workspace_path}': {e}")

        for entry in top_level_entries:
            if should_ignore(entry, workspace_path, atlas_spec):
                continue

            if entry.is_dir():
                eligible_count = 0
                for root, dirs, files in os.walk(entry):
                    root_path = Path(root)
                    dirs[:] = [
                        d
                        for d in dirs
                        if not should_ignore(root_path / d, workspace_path, atlas_spec)
                    ]
                    for f in files:
                        f_path = root_path / f
                        if should_ignore(f_path, workspace_path, atlas_spec):
                            continue
                        if f_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                            eligible_count += 1

                if eligible_count > 0:
                    rel = PurePath(entry.relative_to(workspace_path)).as_posix()
                    candidates.append({"path": rel, "eligible_files": eligible_count})
                    total_eligible += eligible_count
            elif entry.is_file():
                if entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                    rel = PurePath(entry.relative_to(workspace_path)).as_posix()
                    candidates.append({"path": rel, "eligible_files": 1})
                    total_eligible += 1

        response = {
            "workspace": str(workspace_path),
            "candidates": candidates,
            "total_eligible_files": total_eligible,
        }
        recommended_mode, recommended_paths, message = _recommend_dry_run_paths(
            candidates, total_eligible
        )
        response["recommended_mode"] = recommended_mode
        if recommended_paths:
            response["recommended_paths"] = recommended_paths
        response["message"] = message
        return json.dumps(response, separators=(",", ":"), ensure_ascii=False)

    print(
        f"[atlas_index] Indexando workspace='{workspace_path}' "
        f"paths={paths or 'all'} full={full}",
        file=sys.stderr,
    )

    if full or not paths:
        result = _spawn_index_subprocess(workspace_path, paths, full)

        response = {
            "workspace": str(workspace_path),
            "indexed_paths": list(paths) if paths else "all",
            "status": result["status"],
            "log_path": result["log_path"],
        }
        if result["status"] == "started":
            response["pid"] = result["pid"]
            response["message"] = (
                "Reindexação iniciada em background (pid="
                f"{result['pid']}). Consulte atlas_status para acompanhar o progresso "
                "(reindexing: true enquanto estiver em andamento)."
            )
        elif result["status"] == "skipped":
            response["reason"] = result["reason"]
            response["message"] = (
                "Outro processo já está reindexando este índice. "
                "Consulte atlas_status para acompanhar o progresso."
            )
        else:
            response["error"] = result["error"]
            response["message"] = "Falha ao iniciar a reindexação em background."

        return json.dumps(response, separators=(",", ":"), ensure_ascii=False)

    stats = index_workspace(workspace_path, INDEX_DIR_PATH, paths=paths, full=full)

    response = {
        "workspace": str(workspace_path),
        "indexed_paths": list(paths) if paths else "all",
        "files_processed": stats.files_processed,
        "files_scanned": stats.files_scanned,
        "files_eligible": stats.files_eligible,
        "files_skipped_unchanged": stats.files_skipped_unchanged,
        "files_removed": stats.files_removed,
        "chunks_persisted": stats.chunks_persisted,
        "chunks_generated": stats.chunks_generated,
        "duration_s": stats.duration_s,
        "git_head_sha": stats.git_head_sha,
        "phase_durations_s": stats.phase_durations_s,
        "graph_strategy": stats.graph_strategy,
        "graph_nodes": stats.graph_nodes,
        "graph_edges": stats.graph_edges,
        "graph_bytes": stats.graph_bytes,
        "graph_html_bytes": stats.graph_html_bytes,
    }
    if stats.skipped_reason:
        response["skipped_reason"] = stats.skipped_reason
        response["message"] = (
            "Outro processo já está reindexando este índice; esta chamada não "
            "alterou o índice. Consulte atlas_status para acompanhar o progresso."
        )

    return json.dumps(response, separators=(",", ":"), ensure_ascii=False)


# --- MCP Resources ---


@app.resource("atlas://status")
def get_status_resource() -> str:
    """
    Read-only alias of the index status, exposed as an MCP resource.
    """
    data = get_status_data()
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _spawn_index_subprocess(
    workspace_path: Path, paths: Optional[list[str]], full: bool
) -> dict:
    """
    Inicia uma indexação em subprocesso separado (`codesteer_atlas.indexer` CLI),
    generalizando o padrão de `_spawn_background_reindex` para aceitar
    `paths`/`full` (DECISAO-002, DECISAO-004).

    Faz pre-check de `is_reindex_locked` para evitar iniciar um subprocesso que
    apenas seria pulado por `index_workspace` ao adquirir o lock.

    Retorna `{"status": "started", "pid": int, "log_path": str}`,
    `{"status": "skipped", "reason": "reindex_in_progress", "log_path": str}` ou
    `{"status": "error", "error": str, "log_path": str}`. Nunca propaga exceções.
    """
    log_path = INDEX_DIR_PATH / "background_reindex.log"

    if is_reindex_locked(INDEX_DIR_PATH):
        return {
            "status": "skipped",
            "reason": "reindex_in_progress",
            "log_path": str(log_path),
        }

    cmd = [
        sys.executable,
        "-m",
        "codesteer_atlas.indexer",
        "--workspace",
        str(workspace_path),
        "--index-dir",
        str(INDEX_DIR_PATH),
    ]
    if full:
        cmd.append("--full")
    for p in paths or []:
        cmd.extend(["--paths", p])

    # No Windows, evita que o subprocesso abra/pisque uma janela de console
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    try:
        # `newline=""` desativa a tradução de newline do modo texto: no Windows o
        # default converteria '\n' em '\r\n' no header, divergindo do output do
        # subprocesso (que escreve '\n' cru no mesmo fd). Mantém LF uniforme em
        # mac/linux/windows.
        with open(log_path, "a", encoding="utf-8", newline="") as log_file:
            # Cabeçalho com data/hora local (com timezone) marcando o início de
            # cada run; `flush` garante que ele anteceda o output do subprocesso.
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            mode = "full" if full else "incremental"
            scope = ", ".join(paths) if paths else "all"
            log_file.write(
                f"\n[{timestamp}] === Reindex iniciado "
                f"(workspace={workspace_path}, modo={mode}, paths={scope}) ===\n"
            )
            log_file.flush()
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                cwd=str(workspace_path),
                creationflags=creationflags,
            )
    except Exception as e:
        return {"status": "error", "error": str(e), "log_path": str(log_path)}

    return {"status": "started", "pid": process.pid, "log_path": str(log_path)}


def _spawn_background_reindex() -> None:
    """
    Dispara uma reindexação incremental (full=False) em um processo separado no
    startup do MCP, para manter o índice atualizado sem bloquear `app.run()`.

    Roda como subprocesso (em vez de thread) porque, em workspaces grandes,
    operações nativas do reindex (LanceDB/tantivy `create_fts_index`,
    fastembed/onnxruntime) podem reter o GIL por longos períodos. Se isso
    acontecesse em uma thread do mesmo processo, o event loop asyncio do
    FastMCP ficaria sem CPU e o servidor MCP pararia de responder a qualquer
    chamada até o reindex terminar. Em processo separado, o reindex não
    compete pelo GIL com o servidor. Nunca propaga exceções [5].
    """
    storage = StorageBackend(index_dir=INDEX_DIR_PATH)

    if not storage.exists():
        print(
            f"[atlas] Nenhum índice existente em {storage.index_dir.resolve()}; "
            "pulando reindex automático no startup.",
            file=sys.stderr,
        )
        return

    workspace_path = _index_workspace_root()
    if not workspace_path.exists():
        print(
            f"[atlas] Workspace '{workspace_path}' não encontrado; "
            "pulando reindex automático.",
            file=sys.stderr,
        )
        return

    try:
        manifest = storage.get_manifest()
        indexed_at = datetime.fromisoformat(manifest.last_indexed_at)
        if indexed_at.tzinfo is None:
            indexed_at = indexed_at.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - indexed_at).total_seconds()
        current_git_sha = get_git_head_sha(workspace_path)
        if (
            age_s < BACKGROUND_REINDEX_MIN_INTERVAL_S
            and manifest.git_head_sha is not None
            and current_git_sha is not None
            and manifest.git_head_sha == current_git_sha
        ):
            print(
                "[atlas] Reindex automático pulado — índice recente e HEAD do Git inalterado.",
                file=sys.stderr,
            )
            return
    except Exception:
        # Falha em heurística de debounce nunca deve impedir o fallback seguro:
        # se não conseguirmos ler o manifest/timestamp, seguimos com o spawn normal.
        pass

    log_path = INDEX_DIR_PATH / "background_reindex.log"
    print(
        f"[atlas] Reindex automático em background iniciado em processo separado "
        f"(workspace={workspace_path}, log={log_path})...",
        file=sys.stderr,
    )

    result = _spawn_index_subprocess(workspace_path, paths=None, full=False)

    if result["status"] == "error":
        print(
            f"[atlas] Erro ao iniciar reindex automático em background: {result['error']}",
            file=sys.stderr,
        )
    elif result["status"] == "skipped":
        print(
            "[atlas] Reindex automático pulado — outro processo já está "
            "reindexando este índice.",
            file=sys.stderr,
        )


def main():
    # Faz o parsing de --index-dir ANTES de app.run() (DECISAO-002).
    # FastMCP/stdio não usa argv adicional, então é seguro interceptar aqui.
    parser = argparse.ArgumentParser(
        prog="atlas-serve", description="CodeSteer Atlas MCP server (stdio)"
    )
    parser.add_argument(
        "--index-dir",
        default=None,
        help="Caminho absoluto ou relativo para o diretório do índice (.code-index).",
    )
    args, _unknown = parser.parse_known_args()

    global INDEX_DIR_PATH
    INDEX_DIR_PATH = resolve_index_dir(cli_arg=args.index_dir)

    # Restaura o stdout original no sys.stdout imediatamente antes do FastMCP rodar
    # O FastMCP capturará a stream stdout limpa para estabelecer o protocolo stdio JSON-RPC
    sys.stdout = original_stdout

    # Dispara reindex incremental em background (processo separado), sem bloquear
    # o startup do MCP nem competir pelo GIL com o event loop [GA-XX]
    _spawn_background_reindex()

    # Roda o servidor MCP stdio de forma síncrona
    app.run()


if __name__ == "__main__":
    main()
