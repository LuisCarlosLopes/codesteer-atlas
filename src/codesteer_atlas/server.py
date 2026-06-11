import sys
import os
import argparse
import json
import threading
import time
from pathlib import Path, PurePath
from typing import Optional

# 1. Salva o stdout original para o transporte do MCP stdio
original_stdout = sys.stdout

# 2. Redireciona sys.stdout para stderr imediatamente para evitar que
# qualquer import de dependência pesada (como lancedb, torch, etc.) polua o stdout
sys.stdout = sys.stderr

# Agora realizamos os imports de forma segura
from fastmcp import FastMCP  # noqa: E402
from codesteer_atlas.config import DEFAULT_INDEX_DIR, SUPPORTED_EXTENSIONS  # noqa: E402
from codesteer_atlas.embeddings import EmbeddingEngine, FASTEMBED_MODEL_NAME  # noqa: E402
from codesteer_atlas.storage import StorageBackend  # noqa: E402
from codesteer_atlas.indexer import (  # noqa: E402
    get_git_head_sha,
    index_workspace,
    load_atlasignore_spec,
    should_ignore,
)

# Inicializa o servidor FastMCP
app = FastMCP("CodeSteer Atlas")

# Nome do diretório padrão do índice (usado tanto para discovery quanto para
# resolução do workspace pai do índice)
INDEX_DIR_NAME = ".code-index"


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

    Se nenhum mecanismo resolver, retorna `DEFAULT_INDEX_DIR` relativo ao CWD
    (o caller decide como reportar a ausência do índice).
    """
    if cli_arg:
        resolved = Path(cli_arg)
        print(f"[atlas] Índice resolvido via --index-dir: {resolved}", file=sys.stderr)
        return resolved

    env = env if env is not None else os.environ
    env_value = env.get("ATLAS_INDEX_DIR")
    if env_value:
        resolved = Path(env_value)
        print(f"[atlas] Índice resolvido via ATLAS_INDEX_DIR: {resolved}", file=sys.stderr)
        return resolved

    # Discovery ascendente a partir do CWD (ou start_dir informado)
    current = (start_dir or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        candidate_index = candidate / INDEX_DIR_NAME
        if candidate_index.exists():
            print(
                f"[atlas] Índice resolvido via discovery ascendente: {candidate_index}",
                file=sys.stderr,
            )
            return candidate_index

    print(
        "[atlas] AVISO: não foi possível resolver o diretório do índice "
        f"(--index-dir, ATLAS_INDEX_DIR e discovery a partir de {current} falharam). "
        f"Usando o padrão: {DEFAULT_INDEX_DIR}",
        file=sys.stderr,
    )
    return DEFAULT_INDEX_DIR


def _index_not_found_error(storage: "StorageBackend") -> FileNotFoundError:
    """Mensagem de erro acionável listando os 3 mecanismos de resolução do índice."""
    return FileNotFoundError(
        f"Índice local não encontrado em '{storage.index_dir.resolve()}'. "
        "O diretório do índice é resolvido na seguinte ordem: "
        "(1) argumento --index-dir do servidor, "
        "(2) variável de ambiente ATLAS_INDEX_DIR, "
        "(3) busca ascendente por uma pasta '.code-index' a partir do CWD. "
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

    if not storage.exists():
        return {
            "index_exists": False,
            "total_chunks": 0,
            "repos_indexed": [],
            "embedding_model": FASTEMBED_MODEL_NAME,
            "embedding_backend": "fastembed",
            "storage_backend": "lancedb",
            "index_path": str(storage.index_dir.resolve()),
            "last_indexed_at": None,
            "git_head_sha": None,
            "is_stale": False,
            "languages_indexed": [],
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
            "last_indexed_at": manifest.last_indexed_at,
            "git_head_sha": manifest.git_head_sha,
            "is_stale": is_stale,
            "languages_indexed": manifest.languages_indexed,
        }
    except Exception as e:
        print(f"Erro ao ler diagnóstico do índice: {e}", file=sys.stderr)
        return {"index_exists": True, "error": str(e)}


# --- MCP Tools ---


@app.tool()
def atlas_search(
    query: str,
    top_k: int = 5,
    repo: Optional[str] = None,
    language: Optional[str] = None,
    path_prefix: Optional[str] = None,
    include_content: bool = True,
    limit: Optional[int] = None,
) -> str:
    """
    Perform a semantic hybrid search on the indexed source code.

    This tool combines vector similarity (cosine) and full-text keyword search (BM25)
    to find the most relevant code snippets (chunks) based on the user's query intent.
    Use this tool when looking for specific implementations, where functions or features
    are defined, or to locate code matching a certain concept or task.

    Args:
        query: The natural language search term or description of the code to find.
        top_k: Maximum number of results to return (integer between 1 and 50). Defaults to 5.
        repo: Optional repository name to filter results.
        language: Optional programming language to filter results (e.g., 'python', 'javascript', 'go').
        path_prefix: Optional file path prefix to restrict the search to a specific directory (e.g., 'src/controllers').
        include_content: When false, omits the 'content' field from results to save tokens,
            returning only metadata and location (file_path, lines, symbol, type, language, score).
            Defaults to true.
        limit: Alias for 'top_k', accepted for compatibility. When provided, overrides 'top_k'.
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
        serialized_results.append(item)

    response = {
        "results": serialized_results,
        "total_chunks_searched": manifest.total_chunks,
        "query_time_ms": round(query_time_ms, 2),
    }

    return json.dumps(response, separators=(",", ":"), ensure_ascii=False)


@app.tool()
def atlas_map(
    repo: Optional[str] = None,
    path_prefix: Optional[str] = None,
    max_depth: int = 3,
    query: Optional[str] = None,  # Aceito para compatibilidade com clientes MCP que injetam 'query'
) -> str:
    """
    Retrieve a structured hierarchical tree map of classes, methods, and functions in the workspace.

    This tool provides a compact overview of the codebase's architecture and logical structure.
    Use this tool to understand how the project is organized, list classes and methods within folders,
    or find entrypoints without retrieving full file contents. This is extremely token-efficient.

    Args:
        repo: Optional repository name to filter the map.
        path_prefix: Optional file path prefix to filter the map to a specific directory.
        max_depth: Maximum directory depth level in the hierarchical map. Defaults to 3.
        query: Optional search query (accepted for client compatibility and ignored).
    """
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
def atlas_status() -> str:
    """
    Get diagnostic metadata and health status of the local vector index.

    This tool returns information such as whether the index exists, total indexed chunks,
    indexed repositories, active embedding model, last indexing timestamp, git HEAD SHA,
    and whether the index is stale compared to the current workspace code.
    """
    data = get_status_data()
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


@app.tool()
def atlas_index(
    workspace: Optional[str] = None,
    paths: Optional[list[str]] = None,
    full: bool = False,
    dry_run: bool = False,
) -> str:
    """
    Index (or re-index) source code into the local search index.

    IMPORTANT: unless the user already specified what to index, call with
    dry_run=true first, show the candidate folders to the user and ASK whether
    to index everything or specific folders. Only then call again with the
    chosen 'paths' (or none for the full workspace).

    Indexing is incremental by default: unchanged files are skipped on
    subsequent calls, making re-runs fast. Use full=true to force a complete
    rebuild ignoring cached file hashes.

    Args:
        workspace: Absolute path to the root directory to index. Defaults to the
            parent directory of the resolved index, or the current working
            directory if no index has been resolved yet. Must exist and be a directory.
        paths: Optional list of subfolder paths, relative to 'workspace', to index
            (e.g. ["src", "docs"]). When omitted, the entire workspace is indexed.
            Each path must resolve to a location inside 'workspace' (no path traversal).
        full: When true, forces a full re-index ignoring cached file hashes.
            Defaults to false (incremental).
        dry_run: When true, does NOT index anything. Instead, returns the
            top-level candidate folders under 'workspace' with a count of
            eligible files in each, so the agent can present them to the user
            before deciding what to index. Defaults to false.
    """
    # Resolve o workspace: parâmetro -> pai do índice resolvido -> CWD
    if workspace:
        workspace_path = Path(workspace).resolve()
    else:
        workspace_path = _index_workspace_root().resolve()
        if not workspace_path.exists():
            workspace_path = Path.cwd().resolve()

    if not workspace_path.exists() or not workspace_path.is_dir():
        raise ValueError(f"O diretório do workspace '{workspace_path}' não existe.")

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
        return json.dumps(response, separators=(",", ":"), ensure_ascii=False)

    print(
        f"[atlas_index] Indexando workspace='{workspace_path}' "
        f"paths={paths or 'all'} full={full}",
        file=sys.stderr,
    )

    stats = index_workspace(workspace_path, INDEX_DIR_PATH, paths=paths, full=full)

    response = {
        "workspace": str(workspace_path),
        "indexed_paths": list(paths) if paths else "all",
        "files_processed": stats.files_processed,
        "files_skipped_unchanged": stats.files_skipped_unchanged,
        "files_removed": stats.files_removed,
        "chunks_persisted": stats.chunks_persisted,
        "duration_s": stats.duration_s,
        "git_head_sha": stats.git_head_sha,
    }
    return json.dumps(response, separators=(",", ":"), ensure_ascii=False)


# --- MCP Resources ---


@app.resource("atlas://status")
def get_status_resource() -> str:
    """
    Read-only alias of the index status, exposed as an MCP resource.
    """
    data = get_status_data()
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _background_reindex() -> None:
    """
    Roda uma reindexação incremental (full=False) em background no startup do MCP,
    para manter o índice atualizado sem bloquear `app.run()`. Nunca propaga exceções [5].
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

    print(
        f"[atlas] Reindex automático em background iniciado (workspace={workspace_path})...",
        file=sys.stderr,
    )

    try:
        stats = index_workspace(workspace_path, INDEX_DIR_PATH)
    except Exception as e:
        print(f"[atlas] Erro no reindex automático em background: {e}", file=sys.stderr)
        return

    print(
        "[atlas] Reindex automático em background concluído: "
        f"processed={stats.files_processed} skipped={stats.files_skipped_unchanged} "
        f"removed={stats.files_removed} chunks={stats.chunks_persisted} "
        f"duration={stats.duration_s}s",
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

    # Dispara reindex incremental em background, sem bloquear o startup do MCP [GA-XX]
    threading.Thread(
        target=_background_reindex, daemon=True, name="atlas-startup-reindex"
    ).start()

    # Roda o servidor MCP stdio de forma síncrona
    app.run()


if __name__ == "__main__":
    main()
