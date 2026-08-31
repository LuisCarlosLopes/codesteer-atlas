"""
Testes do watcher de workspace (§3.1 · DECISÃO-004).

Nenhum teste depende de `watchdog` instalado nem inicia subprocesso de indexação
real: o caminho positivo injeta módulos falsos em `sys.modules` e o spawner é
sempre um duplo de teste.
"""

import ast
import inspect
import sys
import threading
import types
from pathlib import Path

import pytest

from codesteer_atlas import watcher
from codesteer_atlas.watcher import (
    WATCH_ACTIVE,
    WATCH_DISABLED,
    WATCH_FAILED,
    WATCH_UNAVAILABLE,
    DebouncedReindexTrigger,
    event_paths,
    start_watcher_if_enabled,
)


class _FakeTimer:
    """Duplo de `threading.Timer` — dispara só quando o teste manda."""

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function()


class _TimerFactory:
    def __init__(self):
        self.timers = []

    def __call__(self, interval, function):
        timer = _FakeTimer(interval, function)
        self.timers.append(timer)
        return timer


class _SpawnSpy:
    def __init__(self, raises=None):
        self.calls = 0
        self._raises = raises

    def __call__(self):
        self.calls += 1
        if self._raises is not None:
            raise self._raises


def _trigger(tmp_path, spawn=None, debounce_s=2.0, index_dir=None):
    factory = _TimerFactory()
    spy = spawn if spawn is not None else _SpawnSpy()
    trigger = DebouncedReindexTrigger(
        tmp_path, spy, debounce_s=debounce_s, timer_factory=factory, index_dir=index_dir
    )
    return trigger, factory, spy


# --- Debounce -----------------------------------------------------------------


def test_debounce_coalesce_n_eventos_em_um_unico_spawn(tmp_path):
    """Rajada de N eventos dentro da janela ⇒ um único disparo."""
    trigger, factory, spy = _trigger(tmp_path)

    for i in range(7):
        trigger.notify(tmp_path / "src" / f"mod_{i}.py")

    assert len(factory.timers) == 1, "cada evento não pode agendar seu próprio disparo"
    assert factory.timers[0].started is True
    assert factory.timers[0].interval == 2.0
    assert spy.calls == 0, "o spawn só acontece depois do debounce"

    factory.timers[0].fire()

    assert spy.calls == 1


def test_evento_apos_o_disparo_agenda_uma_nova_janela(tmp_path):
    trigger, factory, spy = _trigger(tmp_path)

    trigger.notify(tmp_path / "a.py")
    factory.timers[0].fire()
    trigger.notify(tmp_path / "b.py")

    assert len(factory.timers) == 2
    factory.timers[1].fire()
    assert spy.calls == 2


def test_notify_indica_se_agendou(tmp_path):
    trigger, _factory, _spy = _trigger(tmp_path)

    assert trigger.notify(tmp_path / "a.py") is True
    assert trigger.notify(tmp_path / "b.py") is False


def test_cancel_desagenda_o_disparo_pendente(tmp_path):
    trigger, factory, spy = _trigger(tmp_path)
    trigger.notify(tmp_path / "a.py")

    trigger.cancel()

    assert factory.timers[0].cancelled is True
    assert spy.calls == 0


# --- Filtro de eventos (anti-loop) --------------------------------------------


def test_evento_dentro_de_code_index_nao_dispara_spawn(tmp_path):
    """
    `.code-index/` é escrito pela própria indexação; reagir a ele seria um loop
    de realimentação. O filtro é `should_ignore`, o mesmo da varredura.
    """
    trigger, factory, spy = _trigger(tmp_path)

    assert trigger.accepts(tmp_path / ".code-index" / "graph.json") is False
    assert trigger.notify(tmp_path / ".code-index" / "graph.json") is False
    assert trigger.notify(tmp_path / ".code-index" / "background_reindex.log") is False

    assert factory.timers == []
    assert spy.calls == 0


def test_evento_dentro_do_index_dir_renomeado_nao_dispara_spawn(tmp_path):
    """
    CR-ALTO-1: com `--index-dir` / `ATLAS_INDEX_DIR` (DECISÃO-002) o índice não se
    chama `.code-index`, logo `IGNORE_DIRS` não o cobre e `graph.json` não começa
    com ponto. A guarda tem de vir do index dir EFETIVAMENTE resolvido.
    """
    index_dir = tmp_path / "meu-indice"
    index_dir.mkdir()
    graph_json = index_dir / "graph.json"

    sem_guarda, _factory, _spy = _trigger(tmp_path)
    assert sem_guarda.accepts(graph_json) is True, (
        "sem o index dir resolvido o evento passa — é exatamente o loop do CR-ALTO-1"
    )

    trigger, factory, spy = _trigger(tmp_path, index_dir=index_dir)

    assert trigger.accepts(graph_json) is False
    assert trigger.notify(graph_json) is False
    assert trigger.notify(index_dir / "manifest.json") is False
    assert trigger.notify(index_dir / "chunks.lance" / "data" / "0.lance") is False

    assert factory.timers == [], "escrita da própria indexação não pode agendar reindexação"
    assert spy.calls == 0


def test_index_dir_renomeado_nao_bloqueia_o_resto_do_workspace(tmp_path):
    """A guarda é do índice, não do workspace: código-fonte segue disparando."""
    index_dir = tmp_path / "meu-indice"
    index_dir.mkdir()
    trigger, factory, _spy = _trigger(tmp_path, index_dir=index_dir)

    assert trigger.notify(tmp_path / "src" / "app.py") is True
    assert len(factory.timers) == 1


def test_evento_em_ignore_dirs_nao_dispara_spawn(tmp_path):
    trigger, factory, spy = _trigger(tmp_path)

    assert trigger.notify(tmp_path / "node_modules" / "pkg" / "index.js") is False
    assert trigger.notify(tmp_path / "__pycache__" / "mod.pyc") is False
    assert trigger.notify(tmp_path / ".git" / "HEAD") is False

    assert factory.timers == []
    assert spy.calls == 0


def test_evento_coberto_por_atlasignore_nao_dispara_spawn(tmp_path):
    (tmp_path / ".atlasignore").write_text("vendor/\n*.log\n", encoding="utf-8")
    trigger, factory, spy = _trigger(tmp_path)

    assert trigger.notify(tmp_path / "vendor" / "lib.py") is False
    assert trigger.notify(tmp_path / "src" / "saida.log") is False
    assert factory.timers == []

    assert trigger.notify(tmp_path / "src" / "app.py") is True
    assert len(factory.timers) == 1


def test_evento_fora_do_workspace_e_descartado(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    trigger, factory, _spy = _trigger(workspace)

    assert trigger.notify(tmp_path / "outro" / "app.py") is False
    assert factory.timers == []


# --- O watcher nunca indexa in-process ----------------------------------------


def test_callback_delega_ao_spawner_e_nunca_chama_index_workspace(tmp_path, monkeypatch):
    """
    DECISÃO-004: indexar na thread do servidor retém o GIL e derruba a
    responsividade do MCP. O trigger só pode chamar o spawner injetado.
    """
    from codesteer_atlas import indexer

    def _boom(*args, **kwargs):
        raise AssertionError("o watcher jamais pode indexar in-process")

    monkeypatch.setattr(indexer, "index_workspace", _boom)

    trigger, factory, spy = _trigger(tmp_path)
    trigger.notify(tmp_path / "src" / "app.py")
    factory.timers[0].fire()

    assert spy.calls == 1


def test_modulo_do_watcher_nao_referencia_indexacao_nem_o_servidor():
    """Garantia estática: o módulo não tem como indexar nem importar `server`."""
    source = inspect.getsource(watcher)

    assert "index_workspace" not in source
    assert "codesteer_atlas.server" not in source

    # Princípio V: nenhum import de topo de módulo pode arrastar `watchdog`.
    topo = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            topo.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            topo.append(node.module or "")
    assert topo, "o módulo precisa ter imports de topo para o teste ser significativo"
    assert not any(nome.startswith("watchdog") for nome in topo)


def test_excecao_do_spawner_nao_propaga_e_e_logada(tmp_path, capsys):
    spy = _SpawnSpy(raises=RuntimeError("subprocesso indisponível"))
    trigger, factory, _ = _trigger(tmp_path, spawn=spy)
    trigger.notify(tmp_path / "src" / "app.py")

    factory.timers[0].fire()

    err = capsys.readouterr().err
    assert "[atlas]" in err
    assert "subprocesso indisponível" in err


# --- event_paths --------------------------------------------------------------


class _FakeEvent:
    def __init__(self, src_path, dest_path=None):
        self.src_path = src_path
        if dest_path is not None:
            self.dest_path = dest_path


def test_event_paths_extrai_src_e_dest(tmp_path):
    origem = str(tmp_path / "a.py")
    destino = str(tmp_path / "b.py")

    assert event_paths(_FakeEvent(origem)) == [Path(origem)]
    assert event_paths(_FakeEvent(origem, destino)) == [Path(origem), Path(destino)]


def test_event_paths_aceita_bytes_e_ignora_vazio(tmp_path):
    origem = str(tmp_path / "a.py")

    assert event_paths(_FakeEvent(origem.encode())) == [Path(origem)]
    assert event_paths(_FakeEvent("")) == []
    assert event_paths(object()) == []


# --- Ativação -----------------------------------------------------------------


def test_flag_ausente_retorna_disabled_sem_efeito_colateral(tmp_path, capsys):
    """Sem `ATLAS_WATCH`, o startup é idêntico ao de hoje: nada roda, nada é logado."""
    spy = _SpawnSpy()

    assert start_watcher_if_enabled(tmp_path, spy, env={}) == WATCH_DISABLED
    assert start_watcher_if_enabled(tmp_path, spy, env={"ATLAS_WATCH": "0"}) == WATCH_DISABLED

    assert capsys.readouterr().err == ""
    assert spy.calls == 0


def test_watchdog_ausente_retorna_unavailable_sem_excecao(tmp_path, monkeypatch, capsys):
    """`watchdog` é extra opcional: sem ele o servidor sobe e o status declara o motivo."""
    for name in ("watchdog", "watchdog.events", "watchdog.observers"):
        monkeypatch.setitem(sys.modules, name, None)

    state = start_watcher_if_enabled(tmp_path, _SpawnSpy(), env={"ATLAS_WATCH": "1"})

    assert state == WATCH_UNAVAILABLE
    err = capsys.readouterr().err
    assert "[atlas]" in err
    assert "watchdog" in err


def _install_fake_watchdog(monkeypatch, observer_cls):
    """Injeta um `watchdog` falso — os testes não podem exigir a dependência opcional."""
    root = types.ModuleType("watchdog")
    events = types.ModuleType("watchdog.events")
    observers = types.ModuleType("watchdog.observers")

    class FileSystemEventHandler:
        pass

    events.FileSystemEventHandler = FileSystemEventHandler
    observers.Observer = observer_cls
    root.events = events
    root.observers = observers

    monkeypatch.setitem(sys.modules, "watchdog", root)
    monkeypatch.setitem(sys.modules, "watchdog.events", events)
    monkeypatch.setitem(sys.modules, "watchdog.observers", observers)


def test_watcher_ativo_agenda_observer_recursivo_no_workspace(tmp_path, monkeypatch, capsys):
    criados = []

    class _FakeObserver:
        def __init__(self):
            self.scheduled = []
            self.started = False
            self.daemon = False
            criados.append(self)

        def schedule(self, handler, path, recursive=False):
            self.scheduled.append((handler, path, recursive))

        def start(self):
            self.started = True

    _install_fake_watchdog(monkeypatch, _FakeObserver)

    state = start_watcher_if_enabled(tmp_path, _SpawnSpy(), env={"ATLAS_WATCH": "1"})

    assert state == WATCH_ACTIVE
    observer = criados[0]
    assert observer.started is True
    assert observer.daemon is True
    _handler, path, recursive = observer.scheduled[0]
    assert path == str(tmp_path)
    assert recursive is True
    assert "[atlas]" in capsys.readouterr().err


def test_handler_ativo_dispara_o_spawner_apenas_para_caminho_relevante(tmp_path, monkeypatch):
    """Fim a fim do caminho positivo: evento → filtro → debounce → spawner injetado."""
    criados = []

    class _FakeObserver:
        def __init__(self):
            self.scheduled = []
            criados.append(self)

        def schedule(self, handler, path, recursive=False):
            self.scheduled.append((handler, path, recursive))

        def start(self):
            pass

    _install_fake_watchdog(monkeypatch, _FakeObserver)

    disparou = threading.Event()

    state = start_watcher_if_enabled(
        tmp_path, disparou.set, env={"ATLAS_WATCH": "1"}, debounce_s=0.01
    )
    assert state == WATCH_ACTIVE

    handler = criados[0].scheduled[0][0]
    handler.on_any_event(_FakeEvent(str(tmp_path / ".code-index" / "graph.json")))
    assert disparou.is_set() is False, "evento em .code-index/ não pode acordar o watcher"

    handler.on_any_event(_FakeEvent(str(tmp_path / "src" / "app.py")))
    assert disparou.wait(10) is True


def test_observer_que_falha_ao_iniciar_retorna_failed(tmp_path, monkeypatch, capsys):
    """Estouro de watches do inotify não pode impedir o servidor de subir."""

    class _BrokenObserver:
        def schedule(self, handler, path, recursive=False):
            pass

        def start(self):
            raise OSError("inotify watch limit reached")

    _install_fake_watchdog(monkeypatch, _BrokenObserver)

    state = start_watcher_if_enabled(tmp_path, _SpawnSpy(), env={"ATLAS_WATCH": "1"})

    assert state == WATCH_FAILED
    err = capsys.readouterr().err
    assert "[atlas]" in err
    assert "inotify watch limit reached" in err


def test_estados_do_watcher_sao_quatro_e_distintos():
    """Princípio VI: o vocabulário de estado é fechado e declarado."""
    estados = {WATCH_ACTIVE, WATCH_DISABLED, WATCH_UNAVAILABLE, WATCH_FAILED}

    assert estados == {"active", "disabled", "unavailable", "failed"}


@pytest.mark.parametrize("valor", ["", "true", "yes", "2"])
def test_apenas_o_valor_1_liga_o_watcher(tmp_path, valor):
    assert start_watcher_if_enabled(tmp_path, _SpawnSpy(), env={"ATLAS_WATCH": valor}) == (
        WATCH_DISABLED
    )


def test_debounce_default_vem_da_constante_central():
    from codesteer_atlas.config import WATCH_DEBOUNCE_S

    assert inspect.signature(start_watcher_if_enabled).parameters["debounce_s"].default == (
        WATCH_DEBOUNCE_S
    )
    assert (
        inspect.signature(DebouncedReindexTrigger.__init__).parameters["debounce_s"].default
        == WATCH_DEBOUNCE_S
    )


def test_timer_do_debounce_e_daemon(tmp_path):
    """Timer pendente não pode segurar o encerramento do servidor."""
    trigger, factory, _spy = _trigger(tmp_path)
    trigger.notify(tmp_path / "src" / "app.py")

    assert factory.timers[0].daemon is True
