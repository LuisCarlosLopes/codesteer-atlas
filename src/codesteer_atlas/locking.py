import sys
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from codesteer_atlas.config import REINDEX_LOCK_FILENAME


@contextmanager
def reindex_lock(index_dir: Path):
    """
    Lock não-bloqueante (timeout=0) entre processos para coordenar reindexações
    do mesmo `.code-index` (DECISAO-001). Yields `True` se adquiriu o lock,
    `False` se outro processo já o detém.
    """
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(index_dir / REINDEX_LOCK_FILENAME), timeout=0)

    acquired = False
    try:
        lock.acquire()
        acquired = True
    except Timeout:
        acquired = False

    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def is_reindex_locked(index_dir: Path) -> bool:
    """
    Verifica (best-effort, sem manter o lock) se outro processo já detém o
    lock de reindex de `index_dir`.
    """
    index_dir = Path(index_dir)
    if not index_dir.exists():
        return False

    lock = FileLock(str(index_dir / REINDEX_LOCK_FILENAME), timeout=0)
    try:
        lock.acquire()
        lock.release()
        return False
    except Timeout:
        return True
    except OSError as e:
        # No Windows o probe pode falhar com PermissionError/OSError (lockfile
        # read-only, antivírus segurando o handle) em vez de Timeout; o status
        # não deve quebrar por causa do probe — assume não bloqueado e loga
        print(
            f"[atlas] Probe do reindex lock falhou em '{index_dir}': {e}",
            file=sys.stderr,
        )
        return False
