from filelock import FileLock
from codesteer_atlas.config import REINDEX_LOCK_FILENAME
from codesteer_atlas.locking import is_reindex_locked, reindex_lock


def test_reindex_lock_acquires_when_free(tmp_path):
    """`reindex_lock` adquire o lock quando ninguém o detém, yields True."""
    with reindex_lock(tmp_path) as acquired:
        assert acquired is True


def test_reindex_lock_returns_false_when_externally_held(tmp_path):
    """`reindex_lock` retorna False (sem bloquear) quando outro FileLock já detém o lock."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    external_lock = FileLock(str(tmp_path / REINDEX_LOCK_FILENAME), timeout=0)
    external_lock.acquire()
    try:
        with reindex_lock(tmp_path) as acquired:
            assert acquired is False
    finally:
        external_lock.release()


def test_reindex_lock_creates_index_dir_if_missing(tmp_path):
    """`reindex_lock` cria `index_dir` automaticamente se ainda não existir."""
    index_dir = tmp_path / "does-not-exist-yet"
    assert not index_dir.exists()

    with reindex_lock(index_dir) as acquired:
        assert acquired is True
        assert index_dir.exists()


def test_reindex_lock_releases_on_exit_allowing_reacquisition(tmp_path):
    """`reindex_lock` libera o lock ao sair do `with`, permitindo nova aquisição."""
    with reindex_lock(tmp_path) as acquired_first:
        assert acquired_first is True

    with reindex_lock(tmp_path) as acquired_second:
        assert acquired_second is True


def test_is_reindex_locked_false_when_dir_missing(tmp_path):
    """`is_reindex_locked` retorna False quando `index_dir` não existe."""
    index_dir = tmp_path / "missing"
    assert is_reindex_locked(index_dir) is False


def test_is_reindex_locked_false_when_free(tmp_path):
    """`is_reindex_locked` retorna False quando ninguém detém o lock."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    assert is_reindex_locked(tmp_path) is False


def test_is_reindex_locked_true_when_externally_held(tmp_path):
    """`is_reindex_locked` retorna True quando outro FileLock detém o lock,
    sem adquiri-lo permanentemente."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    external_lock = FileLock(str(tmp_path / REINDEX_LOCK_FILENAME), timeout=0)
    external_lock.acquire()
    try:
        assert is_reindex_locked(tmp_path) is True
    finally:
        external_lock.release()

    # Após liberar o lock externo, deve poder ser adquirido normalmente
    with reindex_lock(tmp_path) as acquired:
        assert acquired is True


def test_is_reindex_locked_false_and_logs_when_probe_raises_oserror(
    monkeypatch, tmp_path, capsys
):
    """`is_reindex_locked` não propaga OSError do probe (ex.: PermissionError no
    Windows com lockfile read-only ou antivírus segurando o handle) — retorna
    False best-effort e loga em stderr, para o `atlas_status` nunca quebrar."""
    tmp_path.mkdir(parents=True, exist_ok=True)

    def raise_permission_error(self, *args, **kwargs):
        raise PermissionError("lockfile read-only")

    monkeypatch.setattr(FileLock, "acquire", raise_permission_error)

    assert is_reindex_locked(tmp_path) is False
    assert "Probe do reindex lock falhou" in capsys.readouterr().err
