import hashlib
import os
import sys
import time
from pathlib import Path, PurePath
from typing import List, Optional
import click
import pathspec
from codesteer_atlas.config import (
    ATLASIGNORE_FILENAME,
    DEFAULT_INDEX_DIR,
    IGNORE_DIRS,
    MAX_FILE_SIZE,
    SUPPORTED_EXTENSIONS,
)
from codesteer_atlas.chunker import ASTChunker
from codesteer_atlas.embeddings import EmbeddingEngine
from codesteer_atlas.graph import build_and_write
from codesteer_atlas.locking import reindex_lock
from codesteer_atlas.models import IndexStats
from codesteer_atlas.storage import StorageBackend

_PHASE_WEIGHTS = {
    "scan": 0.05,
    "hash": 0.10,
    "chunk": 0.30,
    "embed": 0.45,
    "persist": 0.05,
    "graph": 0.05,
}

_PHASE_LABELS = {
    "scan": "Varredura do workspace",
    "hash": "Verificando alterações",
    "chunk": "Extraindo chunks (AST)",
    "embed": "Gerando embeddings",
    "persist": "Persistindo no LanceDB",
    "graph": "Reconstruindo grafo",
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
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

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
    # Ignora arquivos/pastas ocultos
    if path.name.startswith("."):
        # Permite apenas arquivos específicos se não estiverem na lista de ignorados
        if path.name not in (".code-index",):
            # Se for uma pasta como .git, deve ignorar
            if path.is_dir() or path.parent.name.startswith("."):
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

        return _index_workspace_locked(workspace_path, index_path, paths, full, report_progress)


def _index_workspace_locked(
    workspace_path: Path,
    index_path: Path,
    paths: Optional[List[str]],
    full: bool,
    report_progress: bool,
) -> IndexStats:
    """
    Corpo da indexação executado sob `reindex_lock` (DECISAO-001). Mesma lógica
    de `index_workspace` antes da introdução do lock — varredura, hashing
    incremental, chunking, embeddings e persistência.
    """
    start_time = time.time()
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
    existing_manifest = None
    if storage.exists():
        try:
            existing_manifest = storage.get_manifest()
            existing_files = dict(existing_manifest.files)
            existing_files_meta = dict(existing_manifest.files_meta)
            existing_files_imports = dict(existing_manifest.files_imports)
        except Exception:
            # Manifest incompatível/corrompido: trata como índice vazio (full rebuild)
            existing_files = {}
            existing_files_meta = {}
            existing_files_imports = {}
            existing_manifest = None

    # Varre as subárvores selecionadas
    progress.tick("scan", 0, 1)
    eligible_files, files_ignored_size, _files_ignored_unsupported, stats_by_path = (
        _scan_workspace(workspace_path, scan_roots, atlas_spec)
    )
    progress.tick("scan", 1, 1)
    progress.phase_done("scan")
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

    # Arquivos que estavam no escopo do manifest mas não existem mais (deletados)
    deleted_files = set(files_in_scope_from_manifest.keys()) - set(current_files.keys())
    files_removed = len(deleted_files)

    # Arquivos alterados/novos também precisam ter seus chunks antigos removidos
    # antes de re-inserir (delete + insert)
    files_to_delete_from_index = deleted_files | (
        set(files_to_process.keys()) & set(files_in_scope_from_manifest.keys())
    )

    # Processa (chunking) os arquivos novos/alterados
    all_new_chunks = []
    files_processed = 0
    processed_imports: dict[str, list] = {}

    chunk_total = len(files_to_process)
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
            files_processed += 1
        except Exception as e:
            print(f"Erro ao processar arquivo {file_path}: {e}", file=sys.stderr)
        progress.tick("chunk", chunk_index, chunk_total)

    progress.phase_done("chunk")

    # Gera embeddings em lote apenas para os chunks novos/alterados [GA-06]
    if all_new_chunks:
        chunk_texts = [chunk.content for chunk in all_new_chunks]
        embedding_engine = EmbeddingEngine()

        def _embed_progress(done: int, total: int) -> None:
            progress.tick("embed", done, total)

        vectors = embedding_engine.encode(
            chunk_texts, batch_size=32, on_progress=_embed_progress
        )
        for chunk, vector in zip(all_new_chunks, vectors):
            chunk.vector = vector
    else:
        progress.tick("embed", 1, 1)

    progress.phase_done("embed")

    git_sha = get_git_head_sha(workspace_path)

    # Decide a estratégia de persistência:
    # - Sem índice existente, ou full=True sem paths (reindex completo do workspace):
    #   sobrescreve tudo com os chunks processados nesta execução.
    # - Caso contrário (incremental ou parcial): usa storage para deletar arquivos
    #   alterados/removidos e inserir os novos chunks, preservando o restante.
    progress.tick("persist", 0, 1)
    if existing_manifest is None or (full and not paths):
        storage.store_chunks(all_new_chunks, git_head_sha=git_sha, files_meta=current_meta)
        chunks_persisted = storage.update_manifest_after_incremental(
            files=new_hashes,
            git_head_sha=git_sha,
            files_meta=current_meta,
            files_imports=processed_imports,
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
        for rel in files_to_delete_from_index:
            updated_files.pop(rel, None)
            updated_files_meta.pop(rel, None)
            updated_files_imports.pop(rel, None)
        for rel, file_hash in new_hashes.items():
            if rel in files_to_process:
                updated_files[rel] = file_hash
                updated_files_imports[rel] = processed_imports.get(rel, [])
        # [mtime, size] de todos os arquivos atuais é sempre atualizado, mesmo
        # para arquivos pulados via fast path [P01]
        updated_files_meta.update(current_meta)

        chunks_persisted = storage.update_manifest_after_incremental(
            files=updated_files,
            git_head_sha=git_sha,
            files_meta=updated_files_meta,
            files_imports=updated_files_imports,
        )
        manifest = storage.get_manifest()

    progress.tick("persist", 1, 1)
    progress.phase_done("persist")

    progress.tick("graph", 0, 1)
    try:
        build_and_write(storage, manifest, index_path)
    except Exception as e:
        print(f"[atlas] Falha ao reconstruir graph.json: {e}", file=sys.stderr)
    progress.tick("graph", 1, 1)
    progress.phase_done("graph")
    progress.finish()

    duration_s = time.time() - start_time

    return IndexStats(
        files_processed=files_processed,
        files_skipped_unchanged=files_skipped_unchanged,
        files_removed=files_removed,
        chunks_persisted=chunks_persisted,
        duration_s=round(duration_s, 3),
        git_head_sha=git_sha,
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

    click.echo("\n--- Indexação Concluída com Sucesso! ---")
    click.echo(f"Diretório do índice: {index_path}")
    click.echo(f"Arquivos processados (novos/alterados): {stats.files_processed}")
    click.echo(f"Arquivos inalterados (pulados): {stats.files_skipped_unchanged}")
    click.echo(f"Arquivos removidos do índice: {stats.files_removed}")
    click.echo(f"Total de chunks persistidos: {stats.chunks_persisted}")
    click.echo(f"Git HEAD SHA: {stats.git_head_sha}")
    click.echo(f"Tempo de execução: {stats.duration_s:.2f} segundos.")


if __name__ == "__main__":
    cli()
