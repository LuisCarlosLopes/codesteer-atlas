"""
Testes da ingestão SCIP (§3.2).

Nenhum toolchain SCIP está instalado nesta máquina, então a fixture `index.scip` é
construída por um encoder mínimo simétrico ao leitor e a invocação do subprocesso é
mockada. Nenhum teste depende de binário externo.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from codesteer_atlas.scip_ingest import (
    SCIP_STATUS_OK,
    SCIP_STATUS_PARSE_FAILED,
    SCIP_STATUS_TIMEOUT,
    SCIP_STATUS_TOOLCHAIN_MISSING,
    ScipParseError,
    build_call_edges,
    detect_toolchains,
    ingest_workspace,
    parse_scip_index,
    read_scip_index,
    run_indexer,
)

# ---------------------------------------------------------------------------
# Encoder mínimo de fixture — simétrico ao leitor de `scip_ingest`
# ---------------------------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def _len_delim(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _varint_field(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def _packed(field: int, values) -> bytes:
    return _len_delim(field, b"".join(_varint(value) for value in values))


def enc_occurrence(symbol, start_line, end_line, definition=False, extra=b"") -> bytes:
    payload = _packed(1, [start_line, 0, end_line, 12])
    payload += _len_delim(2, symbol.encode("utf-8"))
    if definition:
        payload += _varint_field(3, 1)
    return payload + extra


def enc_document(relative_path, occurrences, extra=b"") -> bytes:
    payload = _len_delim(1, relative_path.encode("utf-8"))
    for occurrence in occurrences:
        payload += _len_delim(2, occurrence)
    return payload + extra


def enc_index(documents, extra=b"") -> bytes:
    payload = b"".join(_len_delim(2, document) for document in documents)
    return payload + extra


# Campos que o leitor nunca consulta, um por wire type, para provar o skip.
_UNKNOWN_FIELDS = (
    _varint_field(97, 1234)
    + _len_delim(98, b"payload ignorado")
    + _tag(99, 5)
    + b"\x00\x01\x02\x03"
    + _tag(96, 1)
    + b"\x00" * 8
)


def _sym_node(path, name, start_line, end_line):
    return {
        "id": f"sym:{path}#{name}",
        "kind": "symbol",
        "label": name,
        "file_path": path,
        "lines": [start_line, end_line],
    }


# ---------------------------------------------------------------------------
# TASK-013 — leitor do wire format
# ---------------------------------------------------------------------------


def test_le_relative_path_symbol_range_e_symbol_roles_da_fixture():
    data = enc_index(
        [
            enc_document(
                "src/a.py",
                [
                    enc_occurrence("scip py . . `a`/caller().", 4, 4),
                    enc_occurrence("scip py . . `a`/caller().", 2, 9, definition=True),
                ],
            ),
            enc_document("src/b.py", [enc_occurrence("scip py . . `b`/target().", 0, 5)]),
        ]
    )

    occurrences = parse_scip_index(data)

    assert len(occurrences) == 3
    assert [o.relative_path for o in occurrences] == ["src/a.py", "src/a.py", "src/b.py"]
    assert occurrences[0].symbol == "scip py . . `a`/caller()."
    assert (occurrences[0].start_line, occurrences[0].end_line) == (4, 4)
    assert occurrences[0].is_definition is False
    assert occurrences[1].is_definition is True
    assert (occurrences[1].start_line, occurrences[1].end_line) == (2, 9)


def test_campo_desconhecido_e_pulado_em_todos_os_niveis():
    """Wire format trata campo desconhecido como pulável — evolução do schema não quebra."""
    data = enc_index(
        [
            enc_document(
                "src/a.py",
                [enc_occurrence("sym-a", 1, 3, extra=_UNKNOWN_FIELDS)],
                extra=_UNKNOWN_FIELDS,
            )
        ],
        extra=_UNKNOWN_FIELDS,
    )

    occurrences = parse_scip_index(data)

    assert len(occurrences) == 1
    assert occurrences[0].symbol == "sym-a"
    assert (occurrences[0].start_line, occurrences[0].end_line) == (1, 3)


def test_range_de_tres_elementos_fica_na_mesma_linha():
    payload = _packed(1, [7, 0, 12]) + _len_delim(2, b"sym-a")
    occurrences = parse_scip_index(enc_index([enc_document("src/a.py", [payload])]))

    assert len(occurrences) == 1
    assert (occurrences[0].start_line, occurrences[0].end_line) == (7, 7)


def test_range_ausente_ou_invalido_descarta_a_ocorrencia():
    sem_range = _len_delim(2, b"sym-a")
    curto = _packed(1, [3, 0]) + _len_delim(2, b"sym-b")
    occurrences = parse_scip_index(enc_index([enc_document("src/a.py", [sem_range, curto])]))

    assert occurrences == []


def test_entrada_truncada_levanta_erro_tipado_do_modulo():
    data = enc_index([enc_document("src/a.py", [enc_occurrence("sym-a", 1, 3)])])

    with pytest.raises(ScipParseError):
        parse_scip_index(data[:-4])


def test_read_scip_index_recusa_arquivo_acima_do_teto(tmp_path):
    path = tmp_path / "index.scip"
    path.write_bytes(enc_index([enc_document("src/a.py", [enc_occurrence("sym-a", 1, 3)])]))

    with (
        patch("codesteer_atlas.scip_ingest.SCIP_MAX_INDEX_BYTES", 4),
        pytest.raises(ScipParseError),
    ):
        read_scip_index(path)

    assert len(read_scip_index(path)) == 1


def test_read_scip_index_arquivo_ausente_levanta_erro_tipado(tmp_path):
    with pytest.raises(ScipParseError):
        read_scip_index(tmp_path / "nao-existe.scip")


# ---------------------------------------------------------------------------
# TASK-016 — casamento por contenção e emissão de `calls`
# ---------------------------------------------------------------------------


def _call_fixture():
    """`caller` (src/a.py:2-9) referencia `target`, definido em src/b.py:1-6."""
    return parse_scip_index(
        enc_index(
            [
                enc_document(
                    "src/a.py",
                    [
                        enc_occurrence("sym-caller", 1, 8, definition=True),
                        enc_occurrence("sym-target", 4, 4),
                    ],
                ),
                enc_document("src/b.py", [enc_occurrence("sym-target", 0, 5, definition=True)]),
            ]
        )
    )


def test_emite_calls_com_origin_scip_do_chamador_para_o_definidor():
    nodes = [
        _sym_node("src/a.py", "caller", 2, 9),
        _sym_node("src/b.py", "target", 1, 6),
    ]

    edges, languages = build_call_edges(_call_fixture(), nodes)

    assert edges == [
        {
            "source": "sym:src/a.py#caller",
            "target": "sym:src/b.py#target",
            "kind": "calls",
            "origin": "scip",
            # A referência está em src/a.py, linha 4 (0-indexed no SCIP) → 5.
            "location": {"file_path": "src/a.py", "lines": [5, 5]},
        }
    ]
    assert languages == {"python"}


def test_via_location_aponta_a_linha_da_chamada_e_nao_o_intervalo_do_chamador():
    """
    R-5: a aresta `calls` precisa carregar a localização da ocorrência para que
    `graph._via_location` devolva o ponto de chamada real. Sem `location`, o
    fallback devolveria o intervalo inteiro do chunk chamador (2-9).
    """
    from codesteer_atlas.graph import _via_location

    chamador = _sym_node("src/a.py", "caller", 2, 9)
    nodes = [chamador, _sym_node("src/b.py", "target", 1, 6)]

    (edge,) = build_call_edges(_call_fixture(), nodes)[0]

    # SCIP conta a partir de 0; a referência foi codificada na linha 4 → 5.
    assert edge["location"] == {"file_path": "src/a.py", "lines": [5, 5]}
    assert _via_location(chamador, edge) == {"file_path": "src/a.py", "lines": [5, 5]}
    assert _via_location(chamador, {}) == {"file_path": "src/a.py", "lines": [2, 9]}, (
        "o fallback pelo nó é o comportamento que a `location` da aresta substitui"
    )


def test_via_location_da_aresta_deduplicada_e_a_da_primeira_ocorrencia():
    """Duas referências ao mesmo símbolo colapsam em uma aresta: vence a linha 5."""
    occurrences = parse_scip_index(
        enc_index(
            [
                enc_document(
                    "src/a.py",
                    [enc_occurrence("sym-target", 4, 4), enc_occurrence("sym-target", 6, 7)],
                ),
                enc_document("src/b.py", [enc_occurrence("sym-target", 0, 5, definition=True)]),
            ]
        )
    )
    nodes = [_sym_node("src/a.py", "caller", 2, 9), _sym_node("src/b.py", "target", 1, 6)]

    (edge,) = build_call_edges(occurrences, nodes)[0]

    assert edge["location"] == {"file_path": "src/a.py", "lines": [5, 5]}


def test_ocorrencia_fora_de_qualquer_intervalo_de_chunk_e_descartada():
    """A referência cai na linha 5; o único chunk do arquivo cobre 40-50."""
    nodes = [
        _sym_node("src/a.py", "outro", 40, 50),
        _sym_node("src/b.py", "target", 1, 6),
    ]

    edges, languages = build_call_edges(_call_fixture(), nodes)

    assert edges == []
    assert languages == {"python"}  # só o nó de src/b.py casou


def test_arquivo_sem_nenhum_no_no_grafo_nao_casa_nada():
    edges, languages = build_call_edges(_call_fixture(), [])

    assert edges == []
    assert languages == set()


def test_nao_emite_auto_aresta_quando_definicao_e_referencia_no_mesmo_no():
    occurrences = parse_scip_index(
        enc_index(
            [
                enc_document(
                    "src/a.py",
                    [
                        enc_occurrence("sym-rec", 1, 8, definition=True),
                        enc_occurrence("sym-rec", 4, 4),
                    ],
                )
            ]
        )
    )

    edges, _ = build_call_edges(occurrences, [_sym_node("src/a.py", "rec", 2, 9)])

    assert edges == []


def test_contencao_escolhe_o_intervalo_mais_especifico():
    """Classe e método aninhados: a aresta parte do método, não da classe."""
    nodes = [
        _sym_node("src/a.py", "Service", 1, 30),
        _sym_node("src/a.py", "Service.run", 4, 8),
        _sym_node("src/b.py", "target", 1, 6),
    ]

    edges, _ = build_call_edges(_call_fixture(), nodes)

    assert [edge["source"] for edge in edges] == ["sym:src/a.py#Service.run"]


def test_referencia_sem_definicao_indexada_nao_gera_aresta():
    occurrences = parse_scip_index(
        enc_index([enc_document("src/a.py", [enc_occurrence("sym-externo", 4, 4)])])
    )

    edges, _ = build_call_edges(occurrences, [_sym_node("src/a.py", "caller", 2, 9)])

    assert edges == []


def test_referencias_repetidas_geram_uma_unica_aresta():
    occurrences = parse_scip_index(
        enc_index(
            [
                enc_document(
                    "src/a.py",
                    [enc_occurrence("sym-target", 4, 4), enc_occurrence("sym-target", 6, 6)],
                ),
                enc_document("src/b.py", [enc_occurrence("sym-target", 0, 5, definition=True)]),
            ]
        )
    )
    nodes = [_sym_node("src/a.py", "caller", 2, 9), _sym_node("src/b.py", "target", 1, 6)]

    edges, _ = build_call_edges(occurrences, nodes)

    assert len(edges) == 1


# ---------------------------------------------------------------------------
# TASK-014 — detecção de toolchain
# ---------------------------------------------------------------------------


def test_detect_toolchains_vazio_nesta_maquina():
    """Nenhum indexador SCIP está instalado — verificado; o chamador vira toolchain_missing."""
    assert detect_toolchains(["python", "go", "rust", "typescript"]) == {}


def test_detect_toolchains_encontra_binario_no_path():
    with patch("codesteer_atlas.scip_ingest.shutil.which", return_value="/usr/bin/scip-python"):
        assert detect_toolchains(["python", "markdown"]) == {"python": "/usr/bin/scip-python"}


def test_detect_toolchains_ignora_linguagem_sem_indexador():
    with patch("codesteer_atlas.scip_ingest.shutil.which", return_value="/usr/bin/qualquer"):
        assert detect_toolchains(["java", "csharp", "markdown"]) == {}


# ---------------------------------------------------------------------------
# TASK-015 — invocação em subprocesso
# ---------------------------------------------------------------------------


def _completed(returncode=0, stderr=b""):
    return subprocess.CompletedProcess(args=["scip-python"], returncode=returncode, stderr=stderr)


def test_run_indexer_usa_pipes_devnull_e_flags_de_windows(tmp_path, capsys):
    output = tmp_path / "index.scip"

    def _fake_run(argv, **kwargs):
        output.write_bytes(b"")
        _fake_run.kwargs = kwargs
        _fake_run.argv = argv
        return _completed()

    with patch("codesteer_atlas.scip_ingest.subprocess.run", side_effect=_fake_run):
        status = run_indexer("scip-python", ["index"], tmp_path, output)

    assert status == SCIP_STATUS_OK
    assert _fake_run.argv == ["scip-python", "index", "--output", str(output)]
    assert _fake_run.kwargs["stdin"] is subprocess.DEVNULL
    assert _fake_run.kwargs["stdout"] is subprocess.PIPE
    assert _fake_run.kwargs["stderr"] is subprocess.PIPE
    assert _fake_run.kwargs["timeout"] > 0
    esperado = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    assert _fake_run.kwargs["creationflags"] == esperado
    assert _fake_run.kwargs["cwd"] == str(tmp_path)
    assert capsys.readouterr().out == ""


def test_run_indexer_timeout_vira_status_sem_excecao(tmp_path, capsys):
    with patch(
        "codesteer_atlas.scip_ingest.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="scip-python", timeout=1),
    ):
        status = run_indexer("scip-python", ["index"], tmp_path, tmp_path / "index.scip")

    assert status == SCIP_STATUS_TIMEOUT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SCIP" in captured.err


def test_run_indexer_exit_code_nao_zero_vira_parse_failed(tmp_path, capsys):
    with patch(
        "codesteer_atlas.scip_ingest.subprocess.run",
        return_value=_completed(returncode=2, stderr=b"boom"),
    ):
        status = run_indexer("scip-python", ["index"], tmp_path, tmp_path / "index.scip")

    assert status == SCIP_STATUS_PARSE_FAILED
    assert capsys.readouterr().out == ""


def test_run_indexer_sem_arquivo_de_saida_vira_parse_failed(tmp_path):
    with patch("codesteer_atlas.scip_ingest.subprocess.run", return_value=_completed()):
        status = run_indexer("scip-python", ["index"], tmp_path, tmp_path / "index.scip")

    assert status == SCIP_STATUS_PARSE_FAILED


# ---------------------------------------------------------------------------
# Orquestração — `ingest_workspace`
# ---------------------------------------------------------------------------


def _nodes_do_fixture():
    return [
        _sym_node("src/a.py", "caller", 2, 9),
        _sym_node("src/b.py", "target", 1, 6),
    ]


def _fake_indexer(payload: bytes):
    """Escreve `payload` no caminho de saída, como faria o indexador real."""

    def _run(binary, argv_tail, workspace_root, output_path):
        Path(output_path).write_bytes(payload)
        return SCIP_STATUS_OK

    return _run


def test_ingest_sem_toolchain_reporta_toolchain_missing_e_nenhuma_aresta(tmp_path):
    with patch("codesteer_atlas.scip_ingest.subprocess.run") as run_mock:
        result = ingest_workspace(tmp_path, ["python"], _nodes_do_fixture())

    assert result.status == SCIP_STATUS_TOOLCHAIN_MISSING
    assert result.edges == []
    assert result.languages == []
    run_mock.assert_not_called()


def test_ingest_com_indexador_mockado_produz_arestas_e_declara_a_linguagem(tmp_path):
    payload = enc_index(
        [
            enc_document(
                "src/a.py",
                [
                    enc_occurrence("sym-caller", 1, 8, definition=True),
                    enc_occurrence("sym-target", 4, 4),
                ],
            ),
            enc_document("src/b.py", [enc_occurrence("sym-target", 0, 5, definition=True)]),
        ]
    )
    with (
        patch(
            "codesteer_atlas.scip_ingest.detect_toolchains",
            return_value={"python": "/usr/bin/scip-python"},
        ),
        patch("codesteer_atlas.scip_ingest.run_indexer", side_effect=_fake_indexer(payload)),
    ):
        result = ingest_workspace(tmp_path, ["python"], _nodes_do_fixture())

    assert result.status == SCIP_STATUS_OK
    assert len(result.edges) == 1
    assert result.edges[0]["kind"] == "calls"
    assert result.edges[0]["origin"] == "scip"
    assert result.languages == ["python"]


def test_ingest_com_index_truncado_vira_parse_failed_sem_excecao(tmp_path, capsys):
    truncado = enc_index([enc_document("src/a.py", [enc_occurrence("sym-a", 1, 3)])])[:-4]
    with (
        patch(
            "codesteer_atlas.scip_ingest.detect_toolchains",
            return_value={"python": "/usr/bin/scip-python"},
        ),
        patch("codesteer_atlas.scip_ingest.run_indexer", side_effect=_fake_indexer(truncado)),
    ):
        result = ingest_workspace(tmp_path, ["python"], _nodes_do_fixture())

    assert result.status == SCIP_STATUS_PARSE_FAILED
    assert result.edges == []
    assert capsys.readouterr().out == ""


def test_ingest_com_timeout_do_subprocesso_reporta_timeout(tmp_path):
    with (
        patch(
            "codesteer_atlas.scip_ingest.detect_toolchains",
            return_value={"python": "/usr/bin/scip-python"},
        ),
        patch("codesteer_atlas.scip_ingest.run_indexer", return_value=SCIP_STATUS_TIMEOUT),
    ):
        result = ingest_workspace(tmp_path, ["python"], _nodes_do_fixture())

    assert result.status == SCIP_STATUS_TIMEOUT
    assert result.edges == []
    assert result.languages == []


def test_ingest_roda_scip_typescript_uma_vez_para_js_e_ts(tmp_path):
    chamadas = []

    def _run(binary, argv_tail, workspace_root, output_path):
        chamadas.append((binary, tuple(argv_tail)))
        Path(output_path).write_bytes(enc_index([]))
        return SCIP_STATUS_OK

    with (
        patch(
            "codesteer_atlas.scip_ingest.detect_toolchains",
            return_value={
                "javascript": "/usr/bin/scip-typescript",
                "typescript": "/usr/bin/scip-typescript",
            },
        ),
        patch("codesteer_atlas.scip_ingest.run_indexer", side_effect=_run),
    ):
        result = ingest_workspace(tmp_path, ["javascript", "typescript"], _nodes_do_fixture())

    assert chamadas == [("/usr/bin/scip-typescript", ("index",))]
    assert result.status == SCIP_STATUS_OK
    assert result.edges == []


def test_importar_server_nao_carrega_scip_ingest():
    """
    Princípio V: `server.py` importa `indexer`, que só importa este módulo dentro
    da fase. Rodado em subprocesso porque este arquivo de teste já o importou.
    """
    codigo = (
        "import sys, codesteer_atlas.server\n"
        "assert 'codesteer_atlas.scip_ingest' not in sys.modules, sorted(sys.modules)\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )

    assert resultado.returncode == 0, resultado.stderr.decode("utf-8", errors="replace")
    assert resultado.stdout == b""  # Princípio IV: stdout do protocolo intocado
