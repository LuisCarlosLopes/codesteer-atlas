import calendar
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePath
from typing import Dict, List, Optional

import click
import pathspec

from codesteer_atlas.brief import build_and_write_brief
from codesteer_atlas.chunker import ASTChunker, IncompatibleParserError
from codesteer_atlas.config import (
    ATLASIGNORE_FILENAME,
    DEFAULT_INDEX_DIR,
    GIT_HISTORY_TIMEOUT_S,
    GRAPH_FILENAME,
    IGNORE_DIRS,
    MAX_FILE_SIZE,
    SCIP_ENV_FLAG,
    SUPPORTED_EXTENSIONS,
    resolve_git_history_window,
)
from codesteer_atlas.embeddings import EmbeddingEngine
from codesteer_atlas.graph import (
    apply_history,
    apply_scip_result,
    build_and_write,
    build_and_write_incremental,
    load_graph,
)
from codesteer_atlas.locking import reindex_lock
from codesteer_atlas.models import IndexStats
from codesteer_atlas.origin import OriginResolver
from codesteer_atlas.semantic import (
    ProseGenerator,
    SemanticGeneration,
    build_sidecar,
    load_semantic_sidecar,
    semantic_enabled,
)
from codesteer_atlas.storage import StorageBackend

_PHASE_WEIGHTS = {
    "scan": 0.05,
    "hash": 0.10,
    "chunk": 0.30,
    "embed": 0.45,
    "persist": 0.05,
    "graph": 0.04,
    "brief": 0.01,
}

_PHASE_LABELS = {
    "scan": "Varredura do workspace",
    "hash": "Verificando alterações",
    "chunk": "Extraindo chunks (AST)",
    "embed": "Gerando embeddings",
    "persist": "Persistindo no LanceDB",
    "graph": "Reconstruindo grafo",
    "brief": "Gerando briefing do projeto",
}


class IndexProgressReporter:
    """Reporta progresso ponderado por fase; 100% só ao chamar `finish()`."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._completed_weight = 0.0
        self._last_pct = -1

    def _overall_pct(self, phase: str, current: int, total: int) -> int:
        phase_ratio = (current / total) if total > 0 else 1.0
        raw = (self._completed_weight + _PHASE_WEIGHTS[phase] * phase_ratio) * 100
        return min(int(raw), 99)

    def tick(self, phase: str, current: int, total: int) -> None:
        if not self.enabled:
            return

        pct = self._overall_pct(phase, current, total)
        if pct == self._last_pct and current < total:
            return

        self._last_pct = pct
        label = _PHASE_LABELS[phase]
        suffix = f": {current}/{total}" if total > 1 else ""
        print(f"[atlas] {pct}% — {label}{suffix}", file=sys.stderr, flush=True)

    def phase_done(self, phase: str) -> None:
        self._completed_weight += _PHASE_WEIGHTS[phase]
        self._last_pct = -1

    def finish(self) -> None:
        if not self.enabled:
            return
        print("[atlas] 100% — Indexação concluída", file=sys.stderr, flush=True)


def get_git_head_sha(workspace_path: Path) -> Optional[str]:
    """Obtém o hash SHA do commit HEAD atual do Git de forma segura."""
    import subprocess

    # No Windows: CREATE_NO_WINDOW evita janela de console piscando a cada chamada
    # (hosts MCP GUI), e stdin=DEVNULL evita herdar um handle de stdin inválido em
    # processos sem console (OSError WinError 6)
    # O atributo só existe no Windows; a expressão condicional só avalia esse ramo lá
    creationflags = (
        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    )

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=10,
            creationflags=creationflags,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        # Esperado: workspace fora de um repositório git ou sem commits ainda
        return None
    except Exception as e:
        # Inesperado (git fora do PATH, handle inválido, timeout): loga para não
        # mascarar o motivo de `git_head_sha: null`/`is_stale: false` no status
        print(
            f"[atlas] git rev-parse HEAD falhou em '{workspace_path}': {e}",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# Fase 5.1 — leitura bounded da história local de Git
# @MindContext: mensagens de commit alcançáveis pelo HEAD, ancoradas nos símbolos atuais
# @MindDecision: Git só por subprocesso (padrão de `get_git_head_sha`); nada de SDK,
# rede ou persistência de diff/blob/PR/blame — o hunk é evidência, não dado guardado
# @MindTest: tests/test_indexer.py
# ---------------------------------------------------------------------------

_HISTORY_RECORD_SEP = "\x1e"
_HISTORY_UNIT_SEP = "\x1f"
_HISTORY_LOG_FORMAT = "%x1e%H%x1f%aI%x1f%cI%x1f%s%x1f%b%x1f"
_REVERT_SUBJECT_PREFIX = 'Revert "'
_REVERT_BODY_RE = re.compile(r"This reverts commit ([0-9a-fA-F]{7,40})\.")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _run_git(args: List[str], workspace_path: Path) -> Optional[str]:
    """Executa `git` em subprocesso no mesmo padrão seguro de `get_git_head_sha`."""
    import subprocess

    creationflags = (
        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    )
    try:
        result = subprocess.run(
            # `core.quotePath=false` é local à invocação: sem ele o git escapa caminho
            # não-ASCII em octal e o literal nunca casa `manifest_files`/`symbols_by_file`,
            # perdendo o commit em silêncio. `encoding` fixo evita mojibake no locale ANSI.
            ["git", "-c", "core.quotePath=false", *args],
            cwd=str(workspace_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=GIT_HISTORY_TIMEOUT_S,
            creationflags=creationflags,
        )
        return result.stdout
    except Exception as error:
        print(
            f"[atlas] Leitura de histórico Git indisponível ({type(error).__name__}).",
            file=sys.stderr,
        )
        return None


def _history_cutoff(now: datetime, months: int) -> datetime:
    """Instante-limite da janela temporal; a comparação é inclusiva neste instante."""
    month_index = now.month - 1 - months
    year = now.year + month_index // 12
    month = month_index % 12 + 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)


def _parse_history_log(raw: str) -> List[dict]:
    """Converte a saída de `git log --name-only` em registros ordenados por data desc."""
    entries: List[dict] = []
    for block in raw.split(_HISTORY_RECORD_SEP):
        if not block.strip():
            continue
        parts = block.split(_HISTORY_UNIT_SEP)
        if len(parts) < 6:
            continue
        sha = parts[0].strip()
        if not sha:
            continue
        try:
            committed_dt = datetime.fromisoformat(parts[2].strip())
        except ValueError:
            continue
        files = sorted({line.strip() for line in parts[5].splitlines() if line.strip()})
        entries.append(
            {
                "sha": sha,
                "authored_at": parts[1].strip(),
                "committed_at": parts[2].strip(),
                "committed_dt": committed_dt,
                "subject": parts[3],
                "body": parts[4].strip("\n"),
                "files": files,
            }
        )
    return entries


def _revert_marks(subject: str, body: str) -> tuple[bool, Optional[str]]:
    """Marcação determinística de revert; SHA só quando a mensagem o declara [RF07]."""
    match = _REVERT_BODY_RE.search(body or "")
    is_revert = subject.startswith(_REVERT_SUBJECT_PREFIX) or match is not None
    if not is_revert:
        return False, None
    return True, match.group(1) if match else None


def _select_history_window(
    entries: List[dict], manifest_files: set, cutoff: datetime, max_commits: int
) -> List[dict]:
    """
    Aplica a janela aprovada: por arquivo indexado, os `max_commits` mais recentes
    que também estejam dentro do limite temporal (inclusivo no instante-limite).
    """
    counts: Dict[str, int] = {}
    selected: List[dict] = []
    for entry in entries:
        if entry["committed_dt"] < cutoff:
            continue
        kept = []
        for path in entry["files"]:
            if path not in manifest_files:
                continue
            if counts.get(path, 0) >= max_commits:
                continue
            counts[path] = counts.get(path, 0) + 1
            kept.append(path)
        if kept:
            selected.append({**entry, "indexed_files": kept})
    return selected


def _symbols_by_file(storage) -> Dict[str, List[tuple]]:
    """Intervalos atuais de símbolo (não-markdown) usados na interseção de hunks."""
    symbols: Dict[str, List[tuple]] = {}
    for row in storage.get_graph_projection():
        if row.get("language") == "markdown":
            continue
        symbols.setdefault(row["file_path"], []).append(
            (row["scope_name"], int(row["start_line"]), int(row["end_line"]))
        )
    return symbols


def _parse_touched_ranges(raw: str) -> Dict[str, List[tuple]]:
    """Extrai (arquivo → intervalos pós-imagem) dos cabeçalhos de hunk, sem guardar diff."""
    ranges: Dict[str, List[tuple]] = {}
    current: Optional[str] = None
    for line in raw.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
            continue
        if current is None or not line.startswith("@@"):
            continue
        match = _HUNK_RE.match(line)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        ranges.setdefault(current, []).append((start, start + max(count, 1) - 1))
    return ranges


def _commit_touches(
    workspace_path: Path, sha: str, files: List[str], symbols_by_file: Dict[str, List[tuple]]
) -> tuple[List[dict], bool]:
    """
    Ancoragem do commit: interseção entre os hunks atribuíveis e o intervalo ATUAL
    de cada símbolo. Devolve `(touches, confirmado)`; `confirmado=False` quando a
    leitura do commit falhou e a evidência anterior deve ser preservada [GA-010-06].
    """
    raw = _run_git(
        ["show", "--no-color", "--unified=0", "--no-renames", "--format=%x1e", sha, "--", *files],
        workspace_path,
    )
    if raw is None:
        return [], False

    touches = set()
    for path, ranges in _parse_touched_ranges(raw).items():
        for scope_name, start, end in symbols_by_file.get(path, []):
            if any(not (end < hunk_start or start > hunk_end) for hunk_start, hunk_end in ranges):
                touches.add((path, scope_name))
    return [{"file_path": path, "scope_name": scope} for path, scope in sorted(touches)], True


def collect_git_history(
    workspace_path: Path,
    repo_name: str,
    manifest,
    storage,
    now: Optional[datetime] = None,
) -> dict:
    """
    Lê a janela aprovada do histórico alcançável pelo HEAD e devolve
    `{"status", "records"}`. `status="unavailable"` significa que nada foi lido e
    o snapshot ativo deve ser preservado — a indexação estrutural não é afetada [RF09].
    """
    max_commits, max_months = resolve_git_history_window()
    now = now or datetime.now(timezone.utc)
    cutoff = _history_cutoff(now, max_months)

    raw = _run_git(
        [
            "log",
            "--no-color",
            f"--since={(cutoff - timedelta(seconds=1)).isoformat()}",
            f"--format={_HISTORY_LOG_FORMAT}",
            "--name-only",
        ],
        workspace_path,
    )
    if raw is None:
        return {"status": "unavailable", "records": []}

    manifest_files = set(manifest.files.keys())
    selected = _select_history_window(_parse_history_log(raw), manifest_files, cutoff, max_commits)
    symbols_by_file = _symbols_by_file(storage)
    previous = {
        row["id"]: row
        for row in storage.get_history_projection()
        if row.get("repo") == repo_name
    }

    records: List[dict] = []
    partial = False
    for entry in selected:
        touches, confirmed = _commit_touches(
            workspace_path, entry["sha"], entry["indexed_files"], symbols_by_file
        )
        stale = False
        if not confirmed:
            partial = True
            stale = True
            preserved = previous.get(entry["sha"])
            if preserved:
                touches = json.loads(preserved.get("touches_json") or "[]")
        is_revert, reverted = _revert_marks(entry["subject"], entry["body"])
        records.append(
            {
                "id": entry["sha"],
                "repo": repo_name,
                "subject": entry["subject"],
                "body": entry["body"],
                "authored_at": entry["authored_at"],
                "committed_at": entry["committed_at"],
                "files_touched": entry["indexed_files"],
                "touches": touches,
                "is_revert": is_revert,
                "reverted_commit_id": reverted,
                "stale": stale,
            }
        )

    if partial:
        status = "partial"
    elif records:
        status = "ok"
    else:
        status = "empty"
    return {"status": status, "records": records}


def stage_git_history(storage, records: List[dict]) -> Optional[str]:
    """Embute as mensagens e materializa a geração candidata (ainda não ativa)."""
    if records:
        vectors = EmbeddingEngine().encode(
            [f"{record['subject']}\n{record['body']}".strip() for record in records],
            batch_size=32,
        )
        for record, vector in zip(records, vectors, strict=True):
            record["vector"] = vector
    return storage.stage_history(records)


def load_atlasignore_spec(workspace_path: Path) -> Optional[pathspec.PathSpec]:
    """
    Carrega o `.atlasignore` da raiz do workspace (sintaxe gitignore).
    Retorna None se o arquivo não existir ou não puder ser lido (fallback
    silencioso, equivalente a "sem .atlasignore").
    """
    atlasignore_path = workspace_path / ATLASIGNORE_FILENAME
    try:
        with open(atlasignore_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None

    return pathspec.PathSpec.from_lines("gitignore", lines)


def should_ignore(
    path: Path, workspace_path: Path, atlas_spec: Optional[pathspec.PathSpec] = None
) -> bool:
    """Verifica se o arquivo ou diretório deve ser ignorado na indexação."""
    # Ignora ocultos, exceto o próprio `.code-index`: só descarta se for diretório
    # (ex.: .git) ou se já estiver dentro de uma pasta oculta
    if (
        path.name.startswith(".")
        and path.name not in (".code-index",)
        and (path.is_dir() or path.parent.name.startswith("."))
    ):
        return True

    # Verifica se qualquer parte do caminho relativo contém uma pasta ignorada
    try:
        relative_parts = path.relative_to(workspace_path).parts
        for part in relative_parts:
            if part in IGNORE_DIRS:
                return True
    except ValueError:
        pass

    # Filtro adicional opcional via .atlasignore (sintaxe .gitignore)
    if atlas_spec is not None:
        try:
            rel = PurePath(path.relative_to(workspace_path)).as_posix()
            # Diretórios precisam de "/" final para casar padrões como "build/"
            check = rel + "/" if path.is_dir() else rel
            if atlas_spec.match_file(check):
                return True
        except ValueError:
            pass

    return False


def _hash_file_content(file_path: Path) -> Optional[str]:
    """Calcula o hash sha256 do conteúdo binário de um arquivo."""
    try:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def _scan_workspace(
    workspace_path: Path,
    scan_roots: List[Path],
    atlas_spec: Optional[pathspec.PathSpec] = None,
) -> tuple[list[Path], int, int, dict[Path, tuple[float, int]]]:
    """
    Varre recursivamente as raízes informadas (subárvores do workspace) coletando
    arquivos elegíveis para indexação. Retorna (arquivos, ignorados_por_tamanho,
    ignorados_por_extensao_nao_suportada, stats_by_path), onde `stats_by_path`
    mapeia cada arquivo elegível para (mtime, size) já obtidos via `stat()`,
    reaproveitados para evitar reler/hashear arquivos inalterados [P01].
    """
    eligible_files: List[Path] = []
    files_ignored_size = 0
    files_ignored_unsupported = 0
    stats_by_path: dict[Path, tuple[float, int]] = {}

    for scan_root in scan_roots:
        if not scan_root.exists():
            continue

        for root, dirs, files in os.walk(scan_root):
            # Filtra os diretórios in-place para o os.walk não percorrê-los
            dirs[:] = [
                d for d in dirs if not should_ignore(Path(root) / d, workspace_path, atlas_spec)
            ]

            for file in files:
                file_path = Path(root) / file
                if should_ignore(file_path, workspace_path, atlas_spec):
                    continue

                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    files_ignored_unsupported += 1
                    continue

                try:
                    st = file_path.stat()
                    if st.st_size > MAX_FILE_SIZE:
                        print(
                            f"Aviso: Arquivo {file_path.relative_to(workspace_path)} "
                            "ignorado (excede limite de 2MB)",
                            file=sys.stderr,
                        )
                        files_ignored_size += 1
                        continue
                except Exception:
                    continue

                eligible_files.append(file_path)
                stats_by_path[file_path] = (st.st_mtime, st.st_size)

    return eligible_files, files_ignored_size, files_ignored_unsupported, stats_by_path


def _resolve_scan_roots(workspace_path: Path, paths: Optional[List[str]]) -> List[Path]:
    """
    Resolve as raízes de varredura a partir de `paths` (relativos ao workspace),
    validando que cada uma está contida no workspace (anti-traversal).
    Quando `paths` é None, retorna [workspace_path] (workspace inteiro).
    """
    if not paths:
        return [workspace_path]

    scan_roots = []
    for raw_path in paths:
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else workspace_path / candidate
        resolved = resolved.resolve()

        if not resolved.is_relative_to(workspace_path):
            raise ValueError(
                f"O path '{raw_path}' está fora do workspace '{workspace_path}' "
                "(path traversal não é permitido)."
            )

        scan_roots.append(resolved)

    return scan_roots


def index_workspace(
    workspace_path: Path,
    index_path: Path,
    paths: Optional[List[str]] = None,
    full: bool = False,
    report_progress: bool = True,
    *,
    ctx=None,
) -> IndexStats:
    """
    Núcleo reutilizável de indexação (DECISAO-005): varre o workspace (ou as
    subárvores informadas em `paths`), faz parsing AST, gera embeddings e
    persiste no LanceDB de forma incremental por hash de conteúdo [J].

    - `paths=None` indexa o workspace inteiro.
    - `paths=["src", "docs"]` restringe a varredura e a remoção de deletados
      às subárvores selecionadas, preservando o restante do índice.
    - `full=True` ignora os hashes do manifest e força reindexação completa
      das subárvores selecionadas.

    Levanta `ValueError` se `workspace_path` não existir/não for diretório, ou
    se algum `path` resolvido estiver fora do workspace (anti-traversal).

    Adquire `reindex_lock(index_path)` (DECISAO-001): se outro processo já
    detém o lock, retorna imediatamente um `IndexStats` zerado com
    `skipped_reason="reindex_in_progress"`, sem alterar manifest/tabela.
    """
    workspace_path = Path(workspace_path).resolve()
    index_path = Path(index_path).resolve()

    if not workspace_path.exists() or not workspace_path.is_dir():
        raise ValueError(f"O diretório do workspace '{workspace_path}' não existe.")

    # Validação de paths (anti-traversal) ocorre antes do lock para falhar cedo
    _resolve_scan_roots(workspace_path, paths)

    with reindex_lock(index_path) as acquired:
        if not acquired:
            print(
                f"[atlas] Reindex de '{index_path}' já em andamento por outro "
                "processo; pulando esta execução.",
                file=sys.stderr,
            )
            return IndexStats(
                files_processed=0,
                files_skipped_unchanged=0,
                files_removed=0,
                chunks_persisted=0,
                duration_s=0.0,
                git_head_sha=None,
                skipped_reason="reindex_in_progress",
            )

        return _index_workspace_locked(workspace_path, index_path, paths, full, report_progress, ctx=ctx)


def _run_scip_phase(
    workspace_path: Path,
    index_path: Path,
    manifest,
    graph_strategy: str,
    git_sha: Optional[str],
) -> tuple[str, int, Optional[dict]]:
    """
    Fase `scip` (§3.2 · DECISÃO-002). Devolve `(status, arestas, métricas do grafo)`.

    @MindWhy: o import é preguiçoso porque `server.py` importa este módulo — pôr
    `scip_ingest` no topo o arrastaria para o caminho de import do servidor (Princípio V).
    @MindDecision: indexador SCIP é whole-project e leva de dezenas de segundos a
    minutos; roda só em rebuild completo do grafo ou quando o HEAD mudou desde a
    ingestão anterior, senão as arestas `calls` já gravadas são preservadas.
    """
    from codesteer_atlas import scip_ingest

    graph = load_graph(index_path)
    previous = graph.get("scip") or {}
    if graph_strategy != "full" and previous and previous.get("head_sha") == git_sha:
        preserved_status = str(previous.get("status", scip_ingest.SCIP_STATUS_OK))
        return preserved_status, int(previous.get("edges", 0)), None

    result = scip_ingest.ingest_workspace(
        workspace_path, manifest.languages_indexed, graph.get("nodes", [])
    )
    _graph_path, metrics = apply_scip_result(
        index_path=index_path,
        call_edges=result.edges,
        status=result.status,
        head_sha=git_sha,
        languages=result.languages,
    )
    return result.status, len(result.edges), metrics


def _index_workspace_locked(
    workspace_path: Path,
    index_path: Path,
    paths: Optional[List[str]],
    full: bool,
    report_progress: bool,
    *,
    ctx=None,
) -> IndexStats:
    """
    Corpo da indexação executado sob `reindex_lock` (DECISAO-001). Mesma lógica
    de `index_workspace` antes da introdução do lock — varredura, hashing
    incremental, chunking, embeddings e persistência.
    """
    start_time = time.time()
    phase_started_at = time.perf_counter()
    phase_durations_s: dict[str, float] = {}
    progress = IndexProgressReporter(enabled=report_progress)

    scan_roots = _resolve_scan_roots(workspace_path, paths)
    atlas_spec = load_atlasignore_spec(workspace_path)

    repo_name = workspace_path.name
    chunker = ASTChunker()
    storage = StorageBackend(index_dir=index_path)

    # Carrega manifest existente (se houver) para indexação incremental
    existing_files: dict[str, str] = {}
    existing_files_meta: dict[str, list] = {}
    existing_files_imports: dict[str, list] = {}
    existing_files_declares: dict[str, str] = {}
    existing_manifest = None
    if storage.exists():
        try:
            existing_manifest = storage.get_manifest()
            existing_files = dict(existing_manifest.files)
            existing_files_meta = dict(existing_manifest.files_meta)
            existing_files_imports = dict(existing_manifest.files_imports)
            existing_files_declares = dict(existing_manifest.files_declares)
        except Exception:
            # Manifest incompatível/corrompido: trata como índice vazio (full rebuild)
            existing_files = {}
            existing_files_meta = {}
            existing_files_imports = {}
            existing_files_declares = {}
            existing_manifest = None

    # @MindRisk: um recorte legado não pode misturar rows antigas com schema 2.3.0.
    if existing_manifest is not None:
        version = tuple(int(part) if part.isdigit() else 0 for part in existing_manifest.index_version.split("."))
        if version < (2, 3, 0) and not (full and not paths):
            raise RuntimeError(
                f"O índice legado {existing_manifest.index_version} só pode ser convertido "
                "com full=true sem paths; nenhum delete, append ou manifest foi executado."
            )

    # Varre as subárvores selecionadas
    progress.tick("scan", 0, 1)
    eligible_files, files_ignored_size, _files_ignored_unsupported, stats_by_path = (
        _scan_workspace(workspace_path, scan_roots, atlas_spec)
    )
    progress.tick("scan", 1, 1)
    progress.phase_done("scan")
    phase_durations_s["scan"] = round(time.perf_counter() - phase_started_at, 4)
    if files_ignored_size:
        print(f"Arquivos ignorados (> 2MB): {files_ignored_size}", file=sys.stderr)

    # Calcula caminhos relativos POSIX (chave do manifest 'files') de cada arquivo elegível,
    # junto com [mtime, size] capturados durante o scan [P01]
    current_files: dict[str, Path] = {}
    current_meta: dict[str, list] = {}
    for file_path in eligible_files:
        rel_posix = PurePath(file_path.relative_to(workspace_path)).as_posix()
        current_files[rel_posix] = file_path
        mtime, size = stats_by_path[file_path]
        current_meta[rel_posix] = [mtime, size]

    # Determina quais paths (do manifest) estão "sob escopo" desta execução,
    # para que a remoção de deletados não afete arquivos fora dos `paths` selecionados
    if paths:
        scan_root_relatives = [
            PurePath(root.relative_to(workspace_path)).as_posix() for root in scan_roots
        ]

        def _in_scope(rel_path: str) -> bool:
            return any(
                rel_path == scope or rel_path.startswith(scope + "/")
                for scope in scan_root_relatives
            )

        files_in_scope_from_manifest = {
            rel: h for rel, h in existing_files.items() if _in_scope(rel)
        }
    else:
        files_in_scope_from_manifest = dict(existing_files)

    # Calcula hashes dos arquivos atuais e decide o que processar
    files_to_process: dict[str, Path] = {}
    files_skipped_unchanged = 0
    new_hashes: dict[str, str] = {}

    hash_total = len(current_files)
    phase_started_at = time.perf_counter()
    if hash_total == 0:
        progress.tick("hash", 1, 1)
    for hash_index, (rel_posix, file_path) in enumerate(current_files.items(), start=1):
        # Fast path [P01]: se mtime+size não mudaram em relação ao manifest anterior,
        # reaproveita o hash existente sem reler/hashear o conteúdo do arquivo.
        # Reduz drasticamente o custo do reindex incremental em workspaces grandes.
        old_hash = files_in_scope_from_manifest.get(rel_posix)
        if (
            not full
            and old_hash is not None
            and existing_files_meta.get(rel_posix) == current_meta[rel_posix]
        ):
            new_hashes[rel_posix] = old_hash
            files_skipped_unchanged += 1
            progress.tick("hash", hash_index, hash_total)
            continue

        file_hash = _hash_file_content(file_path)
        if file_hash is None:
            progress.tick("hash", hash_index, hash_total)
            continue

        new_hashes[rel_posix] = file_hash

        if not full and old_hash == file_hash:
            files_skipped_unchanged += 1
            progress.tick("hash", hash_index, hash_total)
            continue

        files_to_process[rel_posix] = file_path
        progress.tick("hash", hash_index, hash_total)

    progress.phase_done("hash")
    phase_durations_s["hash"] = round(time.perf_counter() - phase_started_at, 4)

    # Arquivos que estavam no escopo do manifest mas não existem mais (deletados)
    deleted_files = set(files_in_scope_from_manifest.keys()) - set(current_files.keys())
    files_removed = len(deleted_files)

    # Arquivos alterados/novos também precisam ter seus chunks antigos removidos
    # antes de re-inserir (delete + insert)
    files_to_delete_from_index = deleted_files | (
        set(files_to_process.keys()) & set(files_in_scope_from_manifest.keys())
    )

    if existing_manifest is not None and not (full and not paths):
        # Valida antes de chunking/geração e, principalmente, antes do delete.
        storage.validate_incremental_schema()

    # O cache é proporcional ao recorte que será substituído; arquivos inalterados
    # permanecem persistidos e não precisam materializar vetores semânticos.
    semantic_cache = (
        storage.get_semantic_cache(sorted(files_to_delete_from_index))
        if existing_manifest is not None and files_to_delete_from_index
        else {}
    )

    # Processa (chunking) os arquivos novos/alterados
    all_new_chunks = []
    files_processed = 0
    files_failed = 0
    processed_imports: dict[str, list] = {}
    processed_declares: dict[str, str] = {}

    chunk_total = len(files_to_process)
    phase_started_at = time.perf_counter()
    if chunk_total == 0:
        progress.tick("chunk", 1, 1)
    for chunk_index, (rel_posix, file_path) in enumerate(files_to_process.items(), start=1):
        try:
            file_chunks = chunker.chunk_file(file_path, repo_name)
            for chunk in file_chunks:
                chunk.file_path = rel_posix
                chunk._file_hash = new_hashes[rel_posix]
            all_new_chunks.extend(file_chunks)
            processed_imports[rel_posix] = chunker.extract_imports(file_path)
            # Só as 4 linguagens de namespace devolvem valor; as demais retornam None
            # e não entram no mapa (DECISÃO-003)
            declared = chunker.extract_package_declaration(file_path)
            if declared:
                processed_declares[rel_posix] = declared
            files_processed += 1
        except IncompatibleParserError:
            # Falha de ambiente, não do arquivo: continuar produziria um índice vazio
            # reportado como sucesso. Aborta a indexação inteira [D]
            raise
        except Exception as e:
            files_failed += 1
            print(f"Erro ao processar arquivo {file_path}: {e}", file=sys.stderr)
        progress.tick("chunk", chunk_index, chunk_total)

    if files_failed:
        print(
            f"[atlas] ATENÇÃO: {files_failed} de {chunk_total} arquivo(s) falharam no "
            "chunking e ficaram FORA do índice.",
            file=sys.stderr,
        )

    progress.phase_done("chunk")
    phase_durations_s["chunk"] = round(time.perf_counter() - phase_started_at, 4)

    # Gera embeddings em lote apenas para os chunks novos/alterados [GA-06]
    phase_started_at = time.perf_counter()
    if all_new_chunks:
        chunk_texts = [chunk.content for chunk in all_new_chunks]
        embedding_engine = EmbeddingEngine()

        def _embed_progress(done: int, total: int) -> None:
            progress.tick("embed", done, total)

        vectors = embedding_engine.encode(
            chunk_texts, batch_size=32, on_progress=_embed_progress
        )
        for chunk, vector in zip(all_new_chunks, vectors, strict=True):
            chunk.vector = vector
    else:
        progress.tick("embed", 1, 1)

    progress.phase_done("embed")
    phase_durations_s["embed"] = round(time.perf_counter() - phase_started_at, 4)

    semantic_generation = SemanticGeneration(status="disabled")
    semantic_resolver = OriginResolver(ctx=ctx)
    if semantic_enabled():
        semantic_generation = ProseGenerator(semantic_resolver).generate_purposes(
            all_new_chunks, semantic_cache
        )
        unchanged_paths = set(current_files) - set(files_to_process)
        semantic_generation.reused += sum(
            1 for key in semantic_cache if key[0] in unchanged_paths
        )
        if semantic_generation.reused and semantic_generation.status == "no_origin":
            semantic_generation.status = "ok"

    git_sha = get_git_head_sha(workspace_path)

    # Decide a estratégia de persistência:
    # - Sem índice existente, ou full=True sem paths (reindex completo do workspace):
    #   sobrescreve tudo com os chunks processados nesta execução.
    # - Caso contrário (incremental ou parcial): usa storage para deletar arquivos
    #   alterados/removidos e inserir os novos chunks, preservando o restante.
    progress.tick("persist", 0, 1)
    phase_started_at = time.perf_counter()
    if existing_manifest is None or (full and not paths):
        storage.store_chunks(
            all_new_chunks,
            git_head_sha=git_sha,
            files_meta=current_meta,
            files_declares=processed_declares,
        )
        chunks_persisted = storage.update_manifest_after_incremental(
            files=new_hashes,
            git_head_sha=git_sha,
            files_meta=current_meta,
            files_imports=processed_imports,
            files_declares=processed_declares,
        )
        manifest = storage.get_manifest()
    else:
        # Remove do índice os chunks de arquivos alterados/removidos dentro do escopo
        if files_to_delete_from_index:
            storage.delete_by_file_paths(sorted(files_to_delete_from_index))

        # Insere os novos chunks (se houver)
        if all_new_chunks:
            storage.append_chunks(all_new_chunks)

        # Atualiza o mapa de arquivos do manifest:
        # - remove deletados
        # - atualiza/insere os processados
        # - mantém os inalterados
        updated_files = dict(existing_manifest.files)
        updated_files_meta = dict(existing_manifest.files_meta)
        updated_files_imports = dict(existing_files_imports)
        updated_files_declares = dict(existing_files_declares)
        for rel in files_to_delete_from_index:
            updated_files.pop(rel, None)
            updated_files_meta.pop(rel, None)
            updated_files_imports.pop(rel, None)
            updated_files_declares.pop(rel, None)
        for rel, file_hash in new_hashes.items():
            if rel in files_to_process:
                updated_files[rel] = file_hash
                updated_files_imports[rel] = processed_imports.get(rel, [])
                # Ausência = arquivo deixou de declarar namespace; a chave some
                updated_files_declares.pop(rel, None)
                if rel in processed_declares:
                    updated_files_declares[rel] = processed_declares[rel]
        # [mtime, size] de todos os arquivos atuais é sempre atualizado, mesmo
        # para arquivos pulados via fast path [P01]
        updated_files_meta.update(current_meta)

        chunks_persisted = storage.update_manifest_after_incremental(
            files=updated_files,
            git_head_sha=git_sha,
            files_meta=updated_files_meta,
            files_imports=updated_files_imports,
            files_declares=updated_files_declares,
        )
        manifest = storage.get_manifest()

    # 4.2 e observabilidade são gravados depois da tabela, para que o snapshot
    # reflita exatamente os propósitos que restaram no índice.
    previous_sidecar = load_semantic_sidecar(index_path)
    try:
        semantic_payload = build_sidecar(
            index_path,
            storage.get_semantic_projection(),
            semantic_generation,
            semantic_resolver,
            previous_sidecar,
        )
    except Exception as error:
        print(f"[atlas] Falha ao gerar semantic.json: {error}", file=sys.stderr)
        semantic_payload = previous_sidecar or {}

    progress.tick("persist", 1, 1)
    progress.phase_done("persist")
    phase_durations_s["persist"] = round(time.perf_counter() - phase_started_at, 4)

    progress.tick("graph", 0, 1)
    phase_started_at = time.perf_counter()
    graph_strategy = "full"
    graph_metrics = {
        "graph_nodes": 0,
        "graph_edges": 0,
        "graph_bytes": 0,
        "graph_html_bytes": 0,
    }
    try:
        changed_file_paths = set(files_to_process.keys())
        previous_file_paths = set(existing_files.keys())
        has_only_existing_code_updates = (
            bool(changed_file_paths)
            and not deleted_files
            and changed_file_paths.issubset(previous_file_paths)
            and all(not path.lower().endswith(".md") for path in changed_file_paths)
        )

        if not changed_file_paths and not deleted_files and (index_path / "graph.json").exists():
            graph_strategy = "skipped-unchanged"
        elif has_only_existing_code_updates and (index_path / "graph.json").exists():
            _graph_path, graph_metrics = build_and_write_incremental(
                index_path=index_path,
                manifest=manifest,
                updated_chunks=all_new_chunks,
                updated_file_paths=changed_file_paths,
                workspace_root=workspace_path,
            )
            graph_strategy = "incremental-code"
        else:
            _graph_path, graph_metrics = build_and_write(
                storage, manifest, index_path, return_metadata=True,
                workspace_root=workspace_path,
            )
    except Exception as e:
        print(f"[atlas] Falha ao reconstruir graph.json: {e}", file=sys.stderr)
    progress.tick("graph", 1, 1)
    progress.phase_done("graph")
    phase_durations_s["graph"] = round(time.perf_counter() - phase_started_at, 4)

    # Fase `scip`: depois do grafo, porque reescreve as arestas `calls` dele. Fica
    # fora de `_PHASE_WEIGHTS` de propósito — os pesos existentes somam 1.0 (há teste
    # de invariante) e a fase é opcional; incluí-la mudaria o progresso de quem não a usa.
    scip_status = "disabled"
    scip_edges = 0
    if os.environ.get(SCIP_ENV_FLAG) == "1":
        phase_started_at = time.perf_counter()
        try:
            scip_status, scip_edges, scip_metrics = _run_scip_phase(
                workspace_path=workspace_path,
                index_path=index_path,
                manifest=manifest,
                graph_strategy=graph_strategy,
                git_sha=git_sha,
            )
            if scip_metrics is not None:
                graph_metrics = scip_metrics
        except Exception as e:
            print(f"[atlas] Falha na fase SCIP: {e}", file=sys.stderr)
            scip_status = "parse_failed"
        phase_durations_s["scip"] = round(time.perf_counter() - phase_started_at, 4)
        print(
            f"[atlas] Fase SCIP: {scip_status} ({scip_edges} arestas 'calls').",
            file=sys.stderr,
        )

    # Fase `history` (F5.1): roda DEPOIS do grafo e, como a fase SCIP, fica fora de
    # `_PHASE_WEIGHTS` — os pesos existentes somam 1.0 e a camada é opcional.
    # @MindRisk: falha aqui não pode derrubar o estrutural nem apagar o snapshot ativo
    phase_started_at = time.perf_counter()
    git_history: dict = {"status": "unavailable", "records": []}
    try:
        git_history = collect_git_history(workspace_path, repo_name, manifest, storage)
        snapshot_id = None
        if git_history["status"] == "unavailable":
            storage.mark_history_state("unavailable")
            # Sem leitura nova, o grafo reprojeta o snapshot ATIVO: nada é fabricado
            # e a camada anterior não se perde no rebuild [GA-010-05].
            history_rows = storage.get_history_projection()
        else:
            snapshot_id = stage_git_history(storage, git_history["records"])
            history_rows = git_history["records"]
        # Curto-circuito: sem projeção nova E sem snapshot ativo, `apply_history`
        # seria uma reescrita byte-idêntica de graph.json + graph.html (~1,7 MB
        # neste repo) — custo que anulava parte do ganho do caminho incremental
        # para quem não usa a camada. `read_history_pointer()` é a guarda barata:
        # ponteiro presente significa camada a substituir ou a remover do grafo.
        if history_rows or storage.read_history_pointer() is not None:
            _graph_path, graph_metrics = apply_history(index_path, history_rows)
        # Ponteiro só depois do grafo: as duas superfícies referem o mesmo snapshot
        if snapshot_id is not None:
            storage.publish_history(
                snapshot_id,
                state="partial" if git_history["status"] == "partial" else "ok",
            )
            storage.gc_history()
    except Exception as error:
        print(f"[atlas] Falha na fase de histórico Git: {error}", file=sys.stderr)
        git_history = {"status": "unavailable", "records": []}
        storage.mark_history_state("unavailable")
    phase_durations_s["history"] = round(time.perf_counter() - phase_started_at, 4)
    history_state = storage.get_history_state()
    print(
        f"[atlas] Fase de histórico Git: {git_history['status']} "
        f"({history_state.get('commits', 0)} commits, {history_state.get('touches', 0)} touches).",
        file=sys.stderr,
    )

    # O brief é sempre reconstruído por inteiro: seu valor está no ranking GLOBAL
    # (top-N camadas/hubs/entrypoints), que uma atualização parcial não preservaria
    progress.tick("brief", 0, 1)
    phase_started_at = time.perf_counter()
    brief_status = "failed"
    brief_metrics = {"brief_bytes": 0, "brief_layers": 0, "brief_entrypoints": 0}
    try:
        graph_for_brief = None
        if (index_path / GRAPH_FILENAME).exists():
            graph_for_brief = load_graph(index_path)
        _brief_path, brief_metrics = build_and_write_brief(
            manifest=manifest,
            index_path=index_path,
            workspace_root=workspace_path,
            graph=graph_for_brief,
            return_metadata=True,
        )
        brief_status = "full" if graph_for_brief is not None else "degraded-no-graph"
    except Exception as e:
        print(f"[atlas] Falha ao gerar brief.json: {e}", file=sys.stderr)
    # tick/phase_done ficam fora do try para que uma falha não dessincronize os pesos
    progress.tick("brief", 1, 1)
    progress.phase_done("brief")
    phase_durations_s["brief"] = round(time.perf_counter() - phase_started_at, 4)
    progress.finish()

    duration_s = time.time() - start_time

    return IndexStats(
        files_processed=files_processed,
        files_failed=files_failed,
        files_scanned=len(current_files),
        files_eligible=len(eligible_files),
        files_skipped_unchanged=files_skipped_unchanged,
        files_removed=files_removed,
        chunks_persisted=chunks_persisted,
        chunks_generated=len(all_new_chunks),
        duration_s=round(duration_s, 3),
        git_head_sha=git_sha,
        phase_durations_s=phase_durations_s,
        graph_strategy=graph_strategy,
        graph_nodes=graph_metrics["graph_nodes"],
        graph_edges=graph_metrics["graph_edges"],
        graph_bytes=graph_metrics["graph_bytes"],
        graph_html_bytes=graph_metrics["graph_html_bytes"],
        scip_status=scip_status,
        scip_edges=scip_edges,
        git_history_status=git_history["status"],
        git_history_commits=int(history_state.get("commits", 0)),
        git_history_touches=int(history_state.get("touches", 0)),
        brief_status=brief_status,
        brief_bytes=brief_metrics["brief_bytes"],
        brief_layers=brief_metrics["brief_layers"],
        brief_entrypoints=brief_metrics["brief_entrypoints"],
        semantic_status=semantic_generation.status,
        semantic_generated=semantic_generation.generated,
        semantic_reused=semantic_generation.reused,
        semantic_file_generated=int((semantic_payload.get("last_generation") or {}).get("semantic_file_generated", 0)),
        semantic_file_reused=int((semantic_payload.get("last_generation") or {}).get("semantic_file_reused", 0)),
        semantic_layer_generated=int((semantic_payload.get("last_generation") or {}).get("semantic_layer_generated", 0)),
        semantic_layer_reused=int((semantic_payload.get("last_generation") or {}).get("semantic_layer_reused", 0)),
        semantic_origin=semantic_payload.get("origin") or semantic_generation.origin,
        semantic_egress=semantic_payload.get("egress") or semantic_generation.egress,
        semantic_origins=list(semantic_payload.get("origins") or semantic_generation.origins),
        semantic_egresses=list(semantic_payload.get("egresses") or semantic_generation.egresses),
    )


@click.command()
@click.option(
    "--workspace", "-w", default=".", help="Caminho do diretório do workspace a ser indexado."
)
@click.option(
    "--index-dir",
    "-i",
    default=str(DEFAULT_INDEX_DIR),
    help="Caminho do diretório de saída para persistência do índice.",
)
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help="Força reindexação completa, ignorando os hashes do manifest (incremental).",
)
@click.option(
    "--paths",
    "-p",
    multiple=True,
    help="Subpasta(s) relativa(s) ao workspace a indexar (pode ser usado múltiplas vezes)."
    " Quando omitido, indexa o workspace inteiro.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    default=False,
    help="Suprime o progresso detalhado por fase durante a indexação.",
)
def cli(workspace: str, index_dir: str, full: bool, paths: tuple, quiet: bool):
    """
    CLI fino que delega para `index_workspace()`: varredura recursiva de arquivos,
    parsing AST, geração de embeddings locais em lote (incremental por hash) e
    persistência no LanceDB.
    """
    workspace_path = Path(workspace).resolve()
    index_path = Path(index_dir).resolve()
    paths_list = list(paths) if paths else None

    if not workspace_path.exists():
        click.echo(f"Erro: O diretório do workspace '{workspace_path}' não existe.", err=True)
        sys.exit(1)

    repo_name = workspace_path.name
    click.echo(f"Iniciando indexação do repositório: {repo_name}")
    click.echo(f"Workspace: {workspace_path}")
    if paths_list:
        click.echo(f"Pastas selecionadas: {', '.join(paths_list)}")
    if full:
        click.echo("Modo: reindexação completa (--full)")

    try:
        stats = index_workspace(
            workspace_path,
            index_path,
            paths=paths_list,
            full=full,
            report_progress=not quiet,
        )
    except ValueError as e:
        click.echo(f"Erro: {e}", err=True)
        sys.exit(1)

    if stats.skipped_reason:
        click.echo("Reindex pulado: outro processo já está reindexando este índice.")
        return

    if stats.files_processed == 0 and stats.chunks_persisted == 0 and stats.files_removed == 0:
        click.echo("Nenhum fragmento de código elegível encontrado para indexação.")

    # Um índice incompleto não pode ser anunciado como sucesso [D]
    if stats.files_failed:
        click.echo("\n--- Indexação Concluída COM FALHAS ---")
    else:
        click.echo("\n--- Indexação Concluída com Sucesso! ---")
    click.echo(f"Diretório do índice: {index_path}")
    click.echo(f"Arquivos processados (novos/alterados): {stats.files_processed}")
    if stats.files_failed:
        click.echo(
            f"Arquivos que FALHARAM no chunking (fora do índice): {stats.files_failed}"
        )
    click.echo(f"Arquivos inalterados (pulados): {stats.files_skipped_unchanged}")
    click.echo(f"Arquivos removidos do índice: {stats.files_removed}")
    click.echo(f"Total de chunks persistidos: {stats.chunks_persisted}")
    click.echo(f"Git HEAD SHA: {stats.git_head_sha}")
    click.echo(f"Tempo de execução: {stats.duration_s:.2f} segundos.")


if __name__ == "__main__":
    cli()
