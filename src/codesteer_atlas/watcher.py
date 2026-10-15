"""
Watcher de workspace (§3.1) — observa alterações e agenda a reindexação.

@MindContext: fecha a janela em que o índice fica velho enquanto o servidor MCP
está no ar; o startup já cobre `git pull` com o servidor desligado.
@MindDecision: DECISÃO-004 (Opção A) — thread daemon do `watchdog` que apenas
observa e agenda; o trabalho pesado sai para o subprocesso injetado. Indexar
in-process reintroduziria o bug já diagnosticado em `_spawn_background_reindex`
(LanceDB/tantivy e fastembed/onnxruntime retêm o GIL e o servidor para de
responder).
@MindWhy: o `import watchdog` mora dentro de `start_watcher_if_enabled`
(Princípio V) e o spawner é injetado — este módulo não importa `server.py` e não
tem acesso a nenhum caminho de indexação.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Callable, Mapping, Optional

from codesteer_atlas.config import WATCH_DEBOUNCE_S, WATCH_ENV_FLAG
from codesteer_atlas.indexer import load_atlasignore_spec, should_ignore

# Estados declarados do watcher (Princípio VI): `atlas_status` sempre diz por que
# o watcher não está observando, em vez de silenciar.
WATCH_DISABLED = "disabled"
WATCH_ACTIVE = "active"
WATCH_UNAVAILABLE = "unavailable"
WATCH_FAILED = "failed"


def event_paths(event: object) -> list[Path]:
    """
    Caminhos afetados por um evento do `watchdog` (`src_path` e, em `moved`,
    `dest_path`). Tipado como `object` de propósito: o tipo real vive na
    dependência opcional, que pode não estar instalada.
    """
    paths = []
    for attr in ("src_path", "dest_path"):
        raw = getattr(event, attr, None)
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
        if isinstance(raw, str) and raw:
            paths.append(Path(raw))
    return paths


class DebouncedReindexTrigger:
    """
    Filtra eventos e coalesce a rajada em um único disparo do spawner.

    @MindSpec: Input: `notify(path)` por evento | Output: no máximo um
    `spawn()` por janela de `debounce_s` | Error: exceção do spawner é logada em
    stderr e nunca sobe para a thread do observer.
    @MindWhy: o filtro reusa `should_ignore` + `load_atlasignore_spec`, as mesmas
    funções da varredura, e adiciona o `index_dir` EFETIVAMENTE resolvido. Só o
    nome literal `.code-index` de `IGNORE_DIRS` não basta: `--index-dir` /
    `ATLAS_INDEX_DIR` (DECISÃO-002) renomeiam o diretório, e as escritas da
    própria indexação voltariam como eventos → loop de reindexação infinito.
    """

    def __init__(
        self,
        workspace_path: Path,
        spawn: Callable[[], object],
        debounce_s: float = WATCH_DEBOUNCE_S,
        timer_factory: Callable[[float, Callable[[], None]], threading.Timer] = threading.Timer,
        index_dir: Optional[Path] = None,
    ) -> None:
        self.workspace_path = Path(workspace_path)
        self._spawn = spawn
        self._debounce_s = debounce_s
        self._timer_factory = timer_factory
        self._atlas_spec = load_atlasignore_spec(self.workspace_path)
        self._index_dir = Path(index_dir).resolve() if index_dir is not None else None
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def accepts(self, path: Path) -> bool:
        """True quando o caminho é relevante para a indexação."""
        try:
            path.relative_to(self.workspace_path)
        except ValueError:
            return False
        if self._is_inside_index_dir(path):
            return False
        return not should_ignore(path, self.workspace_path, self._atlas_spec)

    def _is_inside_index_dir(self, path: Path) -> bool:
        """
        True quando o caminho pertence ao índice resolvido, qualquer que seja o
        nome do diretório. Ambos os lados são resolvidos para que symlinks no
        caminho (ex.: /tmp → /private/tmp) não escapem da comparação.
        """
        if self._index_dir is None:
            return False
        try:
            Path(path).resolve().relative_to(self._index_dir)
        except (ValueError, OSError):
            return False
        return True

    def notify(self, path: Path) -> bool:
        """
        Registra um evento. Retorna True quando ele agendou um disparo novo.

        @MindWhy: a janela NÃO é reiniciada a cada evento — um build rodando
        adiaria a reindexação indefinidamente. O primeiro evento agenda; os
        seguintes são absorvidos pelo disparo já pendente.
        """
        if not self.accepts(path):
            return False

        with self._lock:
            if self._timer is not None:
                return False
            timer = self._timer_factory(self._debounce_s, self._fire)
            self._timer = timer

        timer.daemon = True
        timer.start()
        return True

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self._spawn()
        except Exception as e:
            print(f"[atlas] Watcher: falha ao disparar a reindexação: {e}", file=sys.stderr)

    def cancel(self) -> None:
        """Cancela um disparo pendente (usado no encerramento e nos testes)."""
        with self._lock:
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()


def start_watcher_if_enabled(
    workspace_path: Path,
    spawn: Callable[[], object],
    env: Optional[Mapping[str, str]] = None,
    debounce_s: float = WATCH_DEBOUNCE_S,
    index_dir: Optional[Path] = None,
) -> str:
    """
    Ativa o watcher atrás de `ATLAS_WATCH` e devolve o estado para `atlas_status`.

    @MindSpec: Output: "disabled" | "unavailable" | "failed" | "active" | Error:
    nenhum — falha de ativação vira estado e log em stderr; o servidor sobe.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    if environ.get(WATCH_ENV_FLAG) != "1":
        return WATCH_DISABLED

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as e:
        print(
            f"[atlas] Watcher indisponível: watchdog não instalado ({e}). "
            "Instale o extra opcional 'watch' para habilitar a reindexação automática.",
            file=sys.stderr,
        )
        return WATCH_UNAVAILABLE

    trigger = DebouncedReindexTrigger(
        workspace_path, spawn, debounce_s=debounce_s, index_dir=index_dir
    )

    class _TriggerHandler(FileSystemEventHandler):  # type: ignore[misc, valid-type]
        def on_any_event(self, event: object) -> None:
            for path in event_paths(event):
                trigger.notify(path)

    try:
        observer = Observer()
        observer.daemon = True
        observer.schedule(_TriggerHandler(), str(workspace_path), recursive=True)
        observer.start()
    except Exception as e:
        trigger.cancel()
        print(
            f"[atlas] Watcher não pôde observar '{workspace_path}': {e}. "
            "O índice segue sendo atualizado no startup e por atlas_index.",
            file=sys.stderr,
        )
        return WATCH_FAILED

    print(
        f"[atlas] Watcher ativo em '{workspace_path}' "
        f"(debounce={debounce_s}s; reindexação incremental em subprocesso).",
        file=sys.stderr,
    )
    return WATCH_ACTIVE
