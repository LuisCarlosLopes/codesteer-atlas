import hashlib
import os
import sys
import time
from pathlib import Path, PurePath
from typing import List, Optional
import click
from codesteer_atlas.config import (
    DEFAULT_INDEX_DIR,
    IGNORE_DIRS,
    MAX_FILE_SIZE,
    SUPPORTED_EXTENSIONS,
)
from codesteer_atlas.chunker import ASTChunker
from codesteer_atlas.embeddings import EmbeddingEngine
from codesteer_atlas.models import IndexStats
from codesteer_atlas.storage import StorageBackend


def get_git_head_sha(workspace_path: Path) -> Optional[str]:
    """Obtém o hash SHA do commit HEAD atual do Git de forma segura."""
    import subprocess

    try:
        # Executa git rev-parse HEAD no diretório do workspace
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        # Retorna None caso o git não esteja inicializado ou não existam commits ainda
        return None


def should_ignore(path: Path, workspace_path: Path) -> bool:
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
    workspace_path: Path, scan_roots: List[Path]
) -> tuple[list[Path], int, int]:
    """
    Varre recursivamente as raízes informadas (subárvores do workspace) coletando
    arquivos elegíveis para indexação. Retorna (arquivos, ignorados_por_tamanho,
    ignorados_por_extensao_nao_suportada).
    """
    eligible_files: List[Path] = []
    files_ignored_size = 0
    files_ignored_unsupported = 0

    for scan_root in scan_roots:
        if not scan_root.exists():
            continue

        for root, dirs, files in os.walk(scan_root):
            # Filtra os diretórios in-place para o os.walk não percorrê-los
            dirs[:] = [d for d in dirs if not should_ignore(Path(root) / d, workspace_path)]

            for file in files:
                file_path = Path(root) / file
                if should_ignore(file_path, workspace_path):
                    continue

                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    files_ignored_unsupported += 1
                    continue

                try:
                    file_size = file_path.stat().st_size
                    if file_size > MAX_FILE_SIZE:
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

    return eligible_files, files_ignored_size, files_ignored_unsupported


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
    """
    start_time = time.time()

    workspace_path = Path(workspace_path).resolve()
    index_path = Path(index_path).resolve()

    if not workspace_path.exists() or not workspace_path.is_dir():
        raise ValueError(f"O diretório do workspace '{workspace_path}' não existe.")

    scan_roots = _resolve_scan_roots(workspace_path, paths)

    repo_name = workspace_path.name
    chunker = ASTChunker()
    storage = StorageBackend(index_dir=index_path)

    # Carrega manifest existente (se houver) para indexação incremental
    existing_files: dict[str, str] = {}
    existing_manifest = None
    if storage.exists():
        try:
            existing_manifest = storage.get_manifest()
            existing_files = dict(existing_manifest.files)
        except Exception:
            # Manifest incompatível/corrompido: trata como índice vazio (full rebuild)
            existing_files = {}
            existing_manifest = None

    # Varre as subárvores selecionadas
    eligible_files, files_ignored_size, _files_ignored_unsupported = _scan_workspace(
        workspace_path, scan_roots
    )
    if files_ignored_size:
        print(f"Arquivos ignorados (> 2MB): {files_ignored_size}", file=sys.stderr)

    # Calcula caminhos relativos POSIX (chave do manifest 'files') de cada arquivo elegível
    current_files: dict[str, Path] = {}
    for file_path in eligible_files:
        rel_posix = PurePath(file_path.relative_to(workspace_path)).as_posix()
        current_files[rel_posix] = file_path

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

    for rel_posix, file_path in current_files.items():
        file_hash = _hash_file_content(file_path)
        if file_hash is None:
            continue

        new_hashes[rel_posix] = file_hash

        if not full and files_in_scope_from_manifest.get(rel_posix) == file_hash:
            files_skipped_unchanged += 1
            continue

        files_to_process[rel_posix] = file_path

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

    for rel_posix, file_path in files_to_process.items():
        try:
            file_chunks = chunker.chunk_file(file_path, repo_name)
            for chunk in file_chunks:
                chunk.file_path = rel_posix
                chunk._file_hash = new_hashes[rel_posix]
            all_new_chunks.extend(file_chunks)
            files_processed += 1
        except Exception as e:
            print(f"Erro ao processar arquivo {file_path}: {e}", file=sys.stderr)

    # Gera embeddings em lote apenas para os chunks novos/alterados [GA-06]
    if all_new_chunks:
        chunk_texts = [chunk.content for chunk in all_new_chunks]
        embedding_engine = EmbeddingEngine()
        vectors = embedding_engine.encode(chunk_texts, batch_size=32)
        for chunk, vector in zip(all_new_chunks, vectors):
            chunk.vector = vector

    git_sha = get_git_head_sha(workspace_path)

    # Decide a estratégia de persistência:
    # - Sem índice existente, ou full=True sem paths (reindex completo do workspace):
    #   sobrescreve tudo com os chunks processados nesta execução.
    # - Caso contrário (incremental ou parcial): usa storage para deletar arquivos
    #   alterados/removidos e inserir os novos chunks, preservando o restante.
    if existing_manifest is None or (full and not paths):
        storage.store_chunks(all_new_chunks, git_head_sha=git_sha)
        chunks_persisted = len(all_new_chunks)
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
        for rel in files_to_delete_from_index:
            updated_files.pop(rel, None)
        for rel, file_hash in new_hashes.items():
            if rel in files_to_process:
                updated_files[rel] = file_hash

        chunks_persisted = storage.update_manifest_after_incremental(
            files=updated_files, git_head_sha=git_sha
        )

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
def cli(workspace: str, index_dir: str, full: bool, paths: tuple):
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
    click.echo("Gerando embeddings locais utilizando o modelo all-MiniLM-L6-v2 (fastembed)...")

    try:
        stats = index_workspace(workspace_path, index_path, paths=paths_list, full=full)
    except ValueError as e:
        click.echo(f"Erro: {e}", err=True)
        sys.exit(1)

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
