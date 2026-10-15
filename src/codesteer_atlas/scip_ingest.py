"""
Ingestão de índices SCIP (§3.2) — leitor mínimo do wire format, detecção de
toolchain, invocação em subprocesso e casamento ocorrência → nó do grafo.

@MindContext: transforma o `index.scip` produzido por um indexador externo
(`scip-python`, `scip-typescript`, `scip-go`, `rust-analyzer`) nas arestas
`kind="calls"` do grafo, com resolução de precisão de compilador (Princípio II).
@MindDecision: DECISÃO-001 escolheu ler o wire format do protobuf à mão em vez de
depender do runtime `protobuf` + `scip_pb2.py` vendorizado. Decodificar quatro
campos de um formato estável não é reimplementar resolução de nomes — e evita o
piso de versão que já mordeu este repositório duas vezes (`_CompatParser`, `mcp<2`).
@MindWhy: nenhum símbolo daqui pode entrar no caminho de import de `server.py`
(Princípio V) — `indexer.py` importa este módulo dentro da fase, não no topo.
"""

from __future__ import annotations

import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from codesteer_atlas.config import (
    SCIP_INDEX_FILENAME,
    SCIP_INDEXERS,
    SCIP_MAX_INDEX_BYTES,
    SCIP_TIMEOUT_S,
    SUPPORTED_EXTENSIONS,
)

# Estados declarados da fase (Princípio VI). Não existe um sexto: falha de
# invocação (exit != 0, binário quebrado, saída ausente) termina sem índice
# legível e é reportada como `parse_failed`.
SCIP_STATUS_DISABLED = "disabled"
SCIP_STATUS_TOOLCHAIN_MISSING = "toolchain_missing"
SCIP_STATUS_TIMEOUT = "timeout"
SCIP_STATUS_PARSE_FAILED = "parse_failed"
SCIP_STATUS_OK = "ok"

CALL_EDGE_ORIGIN_SCIP = "scip"

# Flag de saída aceita por scip-python/scip-typescript/scip-go/rust-analyzer.
_OUTPUT_FLAG = "--output"

_STDERR_TAIL_CHARS = 500


class ScipParseError(RuntimeError):
    """Índice SCIP ausente, truncado ou acima de `SCIP_MAX_INDEX_BYTES`."""


# ---------------------------------------------------------------------------
# TASK-013 — leitor mínimo do wire format (DECISÃO-001)
# ---------------------------------------------------------------------------

_WIRE_VARINT = 0
_WIRE_64BIT = 1
_WIRE_LENGTH_DELIMITED = 2
_WIRE_32BIT = 5

# Field numbers do `scip.proto` (Sourcegraph). Em protobuf o número do campo é
# contrato: campo desconhecido é pulável e evolução do schema não quebra o leitor.
_FIELD_INDEX_DOCUMENTS = 2
_FIELD_DOCUMENT_RELATIVE_PATH = 1
_FIELD_DOCUMENT_OCCURRENCES = 2
_FIELD_OCCURRENCE_RANGE = 1
_FIELD_OCCURRENCE_SYMBOL = 2
_FIELD_OCCURRENCE_SYMBOL_ROLES = 3

_SYMBOL_ROLE_DEFINITION = 0x1


@dataclass(frozen=True)
class ScipOccurrence:
    """Ocorrência de símbolo em um documento SCIP. `*_line` é 0-indexed, como no SCIP."""

    relative_path: str
    symbol: str
    start_line: int
    end_line: int
    is_definition: bool


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ScipParseError("varint truncado: fim inesperado do buffer")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ScipParseError("varint maior que 64 bits")


def _read_tag(data: bytes, pos: int) -> Tuple[int, int, int]:
    tag, pos = _read_varint(data, pos)
    return tag >> 3, tag & 0x07, pos


def _read_length_delimited(data: bytes, pos: int) -> Tuple[bytes, int]:
    length, pos = _read_varint(data, pos)
    end = pos + length
    if end > len(data):
        raise ScipParseError("campo length-delimited truncado")
    return data[pos:end], end


def _skip_field(data: bytes, pos: int, wire_type: int) -> int:
    if wire_type == _WIRE_VARINT:
        _, pos = _read_varint(data, pos)
        return pos
    if wire_type == _WIRE_LENGTH_DELIMITED:
        _, pos = _read_length_delimited(data, pos)
        return pos
    if wire_type == _WIRE_64BIT:
        pos += 8
    elif wire_type == _WIRE_32BIT:
        pos += 4
    else:
        raise ScipParseError(f"wire type desconhecido: {wire_type}")
    if pos > len(data):
        raise ScipParseError("campo de tamanho fixo truncado")
    return pos


def _decode_int_list(payload: bytes) -> List[int]:
    values: List[int] = []
    pos = 0
    while pos < len(payload):
        value, pos = _read_varint(payload, pos)
        values.append(value)
    return values


def _parse_occurrence(data: bytes) -> Optional[Tuple[str, int, int, bool]]:
    """
    `(symbol, start_line, end_line, is_definition)` ou None quando o range é
    inutilizável. `range` é `repeated int32`: packed (padrão do proto3) ou não.
    """
    symbol = ""
    line_range: List[int] = []
    roles = 0
    pos = 0
    while pos < len(data):
        field, wire_type, pos = _read_tag(data, pos)
        if field == _FIELD_OCCURRENCE_RANGE and wire_type == _WIRE_LENGTH_DELIMITED:
            payload, pos = _read_length_delimited(data, pos)
            line_range = _decode_int_list(payload)
        elif field == _FIELD_OCCURRENCE_RANGE and wire_type == _WIRE_VARINT:
            value, pos = _read_varint(data, pos)
            line_range.append(value)
        elif field == _FIELD_OCCURRENCE_SYMBOL and wire_type == _WIRE_LENGTH_DELIMITED:
            payload, pos = _read_length_delimited(data, pos)
            symbol = payload.decode("utf-8", errors="replace")
        elif field == _FIELD_OCCURRENCE_SYMBOL_ROLES and wire_type == _WIRE_VARINT:
            roles, pos = _read_varint(data, pos)
        else:
            pos = _skip_field(data, pos, wire_type)

    if not symbol:
        return None
    # [startLine, startChar, endLine, endChar] ou [startLine, startChar, endChar]
    if len(line_range) == 4:
        start_line, end_line = line_range[0], line_range[2]
    elif len(line_range) == 3:
        start_line = end_line = line_range[0]
    else:
        return None
    return symbol, start_line, end_line, bool(roles & _SYMBOL_ROLE_DEFINITION)


def _parse_document(data: bytes) -> List[ScipOccurrence]:
    relative_path = ""
    raw_occurrences: List[bytes] = []
    pos = 0
    while pos < len(data):
        field, wire_type, pos = _read_tag(data, pos)
        if field == _FIELD_DOCUMENT_RELATIVE_PATH and wire_type == _WIRE_LENGTH_DELIMITED:
            payload, pos = _read_length_delimited(data, pos)
            relative_path = payload.decode("utf-8", errors="replace")
        elif field == _FIELD_DOCUMENT_OCCURRENCES and wire_type == _WIRE_LENGTH_DELIMITED:
            payload, pos = _read_length_delimited(data, pos)
            raw_occurrences.append(payload)
        else:
            pos = _skip_field(data, pos, wire_type)

    if not relative_path:
        return []
    normalized = posixpath.normpath(relative_path.replace("\\", "/"))
    occurrences: List[ScipOccurrence] = []
    for raw in raw_occurrences:
        parsed = _parse_occurrence(raw)
        if parsed is None:
            continue
        symbol, start_line, end_line, is_definition = parsed
        occurrences.append(
            ScipOccurrence(
                relative_path=normalized,
                symbol=symbol,
                start_line=start_line,
                end_line=end_line,
                is_definition=is_definition,
            )
        )
    return occurrences


def parse_scip_index(data: bytes) -> List[ScipOccurrence]:
    """Percorre `Index.documents → Document.occurrences`, pulando todo campo desconhecido."""
    occurrences: List[ScipOccurrence] = []
    pos = 0
    while pos < len(data):
        field, wire_type, pos = _read_tag(data, pos)
        if field == _FIELD_INDEX_DOCUMENTS and wire_type == _WIRE_LENGTH_DELIMITED:
            payload, pos = _read_length_delimited(data, pos)
            occurrences.extend(_parse_document(payload))
        else:
            pos = _skip_field(data, pos, wire_type)
    return occurrences


def read_scip_index(path: Path) -> List[ScipOccurrence]:
    """
    Lê o arquivo inteiro uma única vez, sob o teto `SCIP_MAX_INDEX_BYTES`
    (`MAX_FILE_SIZE` não se aplica: é artefato de toolchain, não código indexado).
    """
    path = Path(path)
    if not path.exists():
        raise ScipParseError(f"índice SCIP não encontrado: {path}")
    size = path.stat().st_size
    if size > SCIP_MAX_INDEX_BYTES:
        raise ScipParseError(
            f"índice SCIP de {size} bytes excede SCIP_MAX_INDEX_BYTES ({SCIP_MAX_INDEX_BYTES})"
        )
    return parse_scip_index(path.read_bytes())


# ---------------------------------------------------------------------------
# TASK-014 / TASK-015 — toolchain e invocação
# ---------------------------------------------------------------------------


def detect_toolchains(languages: Iterable[str]) -> Dict[str, str]:
    """Linguagem → caminho absoluto do indexador encontrado no PATH. Vazio ⇒ `toolchain_missing`."""
    found: Dict[str, str] = {}
    for language in sorted(set(languages or ())):
        argv = SCIP_INDEXERS.get(language)
        if not argv:
            continue
        binary = shutil.which(argv[0])
        if binary:
            found[language] = binary
    return found


def run_indexer(
    binary: str,
    argv_tail: Sequence[str],
    workspace_root: Path,
    output_path: Path,
) -> str:
    """
    Invoca o indexador em subprocesso e devolve um status — nunca uma exceção.

    @MindRisk: nenhum byte do subprocesso pode alcançar o `stdout` deste processo
    (Princípio IV), daí `stdout`/`stderr` em PIPE e `stdin=DEVNULL`; no Windows,
    `CREATE_NO_WINDOW` evita janela de console piscando (padrão de `get_git_head_sha`).
    """
    argv = [binary, *argv_tail, _OUTPUT_FLAG, str(output_path)]
    creationflags = (
        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    )
    try:
        completed = subprocess.run(
            argv,
            cwd=str(workspace_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=SCIP_TIMEOUT_S,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[atlas] Indexador SCIP '{binary}' excedeu {SCIP_TIMEOUT_S}s; fase degradada.",
            file=sys.stderr,
        )
        return SCIP_STATUS_TIMEOUT
    except Exception as e:
        print(f"[atlas] Falha ao invocar o indexador SCIP '{binary}': {e}", file=sys.stderr)
        return SCIP_STATUS_PARSE_FAILED

    if completed.returncode != 0:
        tail = (completed.stderr or b"").decode("utf-8", errors="replace")[-_STDERR_TAIL_CHARS:]
        print(
            f"[atlas] Indexador SCIP '{binary}' saiu com código "
            f"{completed.returncode}: {tail}",
            file=sys.stderr,
        )
        return SCIP_STATUS_PARSE_FAILED

    if not Path(output_path).exists():
        print(
            f"[atlas] Indexador SCIP '{binary}' terminou sem gravar {output_path}.",
            file=sys.stderr,
        )
        return SCIP_STATUS_PARSE_FAILED

    return SCIP_STATUS_OK


# ---------------------------------------------------------------------------
# TASK-016 — casamento ocorrência → nó e emissão de `calls`
# ---------------------------------------------------------------------------


def _symbol_ranges_by_file(nodes: Iterable[dict]) -> Dict[str, List[Tuple[int, int, str]]]:
    ranges: Dict[str, List[Tuple[int, int, str]]] = {}
    for node in nodes:
        if node.get("kind") != "symbol":
            continue
        lines = node.get("lines")
        file_path = node.get("file_path")
        if not file_path or not lines or len(lines) != 2:
            continue
        ranges.setdefault(file_path, []).append((int(lines[0]), int(lines[1]), node["id"]))
    # @MindWhy: chunks aninham (classe contém método); ordenar por amplitude faz o
    # intervalo mais específico vencer a contenção.
    for file_ranges in ranges.values():
        file_ranges.sort(key=lambda item: (item[1] - item[0], item[2]))
    return ranges


def _containing_node(file_ranges: List[Tuple[int, int, str]], line: int) -> Optional[str]:
    for start_line, end_line, node_id in file_ranges:
        if start_line <= line <= end_line:
            return node_id
    return None


def _language_of(relative_path: str) -> Optional[str]:
    return SUPPORTED_EXTENSIONS.get(posixpath.splitext(relative_path)[1].lower())


def build_call_edges(
    occurrences: Iterable[ScipOccurrence], nodes: Iterable[dict]
) -> Tuple[List[dict], Set[str]]:
    """
    Arestas `calls` (chamador → definidor) e linguagens cujas ocorrências casaram.

    Ocorrência cuja linha não cai em nenhum intervalo de chunk é descartada — sem
    aresta órfã. Auto-aresta não é emitida, como já faz `graph._add_edge`.

    @MindWhy: cada aresta carrega `location` com o arquivo e a linha da própria
    ocorrência (1-indexed, como os chunks). É o que faz `graph._via_location`
    devolver o ponto de chamada real em `atlas_graph(mode="affected")`, em vez do
    fallback pelo intervalo inteiro do nó chamador.
    """
    ranges_by_file = _symbol_ranges_by_file(nodes)
    definition_node: Dict[str, str] = {}
    references: List[Tuple[str, str, dict]] = []
    matched_languages: Set[str] = set()

    for occurrence in occurrences:
        file_ranges = ranges_by_file.get(occurrence.relative_path)
        if not file_ranges:
            continue
        # SCIP conta linhas a partir de 0; os chunks do índice, a partir de 1.
        node_id = _containing_node(file_ranges, occurrence.start_line + 1)
        if node_id is None:
            continue
        language = _language_of(occurrence.relative_path)
        if language:
            matched_languages.add(language)
        if occurrence.is_definition:
            definition_node.setdefault(occurrence.symbol, node_id)
        else:
            location = {
                "file_path": occurrence.relative_path,
                "lines": [occurrence.start_line + 1, occurrence.end_line + 1],
            }
            references.append((node_id, occurrence.symbol, location))

    edges: List[dict] = []
    seen: Set[Tuple[str, str]] = set()
    for source_id, symbol, location in references:
        target_id = definition_node.get(symbol)
        if target_id is None or target_id == source_id or (source_id, target_id) in seen:
            continue
        seen.add((source_id, target_id))
        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "kind": "calls",
                "origin": CALL_EDGE_ORIGIN_SCIP,
                "location": location,
            }
        )
    edges.sort(key=lambda edge: (edge["source"], edge["target"]))
    return edges, matched_languages


# ---------------------------------------------------------------------------
# Orquestração da ingestão (consumida pela fase `scip` do indexer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScipIngestResult:
    status: str
    edges: List[dict]
    languages: List[str]


def _planned_runs(toolchains: Dict[str, str]) -> List[Tuple[str, Tuple[str, ...]]]:
    """Dedupe por argv: `scip-typescript` cobre javascript e typescript numa execução só."""
    runs: List[Tuple[str, Tuple[str, ...]]] = []
    for language, binary in toolchains.items():
        argv_tail = tuple(SCIP_INDEXERS[language][1:])
        if (binary, argv_tail) not in runs:
            runs.append((binary, argv_tail))
    return runs


def ingest_workspace(
    workspace_root: Path, languages: Iterable[str], nodes: Iterable[dict]
) -> ScipIngestResult:
    """
    Detecta o toolchain, roda os indexadores e devolve as arestas `calls` casadas.

    Nunca levanta: cada modo de falha vira um dos status declarados (Princípio VI).
    """
    toolchains = detect_toolchains(languages)
    if not toolchains:
        return ScipIngestResult(SCIP_STATUS_TOOLCHAIN_MISSING, [], [])

    occurrences: List[ScipOccurrence] = []
    statuses: List[str] = []
    with tempfile.TemporaryDirectory(prefix="atlas-scip-") as tmp_dir:
        for position, (binary, argv_tail) in enumerate(_planned_runs(toolchains)):
            output_path = Path(tmp_dir) / f"{position}-{SCIP_INDEX_FILENAME}"
            status = run_indexer(binary, argv_tail, Path(workspace_root), output_path)
            if status == SCIP_STATUS_OK:
                try:
                    occurrences.extend(read_scip_index(output_path))
                except ScipParseError as e:
                    print(f"[atlas] Índice SCIP ilegível ({binary}): {e}", file=sys.stderr)
                    status = SCIP_STATUS_PARSE_FAILED
            statuses.append(status)

    edges, matched_languages = build_call_edges(occurrences, nodes)
    if matched_languages or SCIP_STATUS_OK in statuses:
        status = SCIP_STATUS_OK
    elif SCIP_STATUS_TIMEOUT in statuses:
        status = SCIP_STATUS_TIMEOUT
    else:
        status = SCIP_STATUS_PARSE_FAILED
    return ScipIngestResult(status, edges, sorted(matched_languages))
