import json
import posixpath
import re
from typing import List, NamedTuple, Optional

from codesteer_atlas.markdown_links import _WIKILINK_PATTERN

_CITE_PATTERN = re.compile(
    r"\b(DECISAO|DECISÃO|ADR|RFC|DEC)[-_ ]?(\d{1,4})\b",
    re.IGNORECASE,
)
_ANNOTATION_PATTERN = re.compile(
    r"^\s*(?:#|//|--|\*)\s*(NOTE|WHY):+\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


class RationaleRef(NamedTuple):
    """Referência de rationale extraída de código/comentários."""

    kind: str
    raw: str
    key: str
    text: Optional[str] = None


def _normalize_cite(prefix: str, number: str) -> str:
    normalized_prefix = prefix.casefold()
    if normalized_prefix in {"decisao", "decisão", "dec"}:
        normalized_prefix = "dec"
    return f"{normalized_prefix}-{int(number):03d}"


def _normalize_wikilink_key(raw_target: str) -> Optional[str]:
    target = raw_target.strip()
    if not target:
        return None
    stem = posixpath.basename(target)
    if "." in stem:
        if not stem.lower().endswith(".md"):
            return None
        stem = stem[: -len(".md")]
    return stem.strip().lower() or None


def serialize_rationale_ref(ref: RationaleRef) -> str:
    """Serializa uma ref para o formato estável persistido no índice."""
    if ref.kind == "annotation":
        return f"{ref.key}:{ref.text or ''}"
    return f"{ref.kind}:{ref.key}"


def deserialize_rationale_ref(value: str) -> Optional[RationaleRef]:
    """Desserializa uma ref persistida no índice."""
    if not value or ":" not in value:
        return None
    prefix, payload = value.split(":", 1)
    if prefix in {"cite", "wikilink"}:
        return RationaleRef(kind=prefix, raw=value, key=payload, text=None)
    if prefix in {"note", "why"}:
        return RationaleRef(kind="annotation", raw=value, key=prefix, text=payload)
    return None


def serialize_rationale_refs(refs: List[RationaleRef]) -> List[str]:
    return [serialize_rationale_ref(ref) for ref in refs]


def encode_references_json(values: List[str]) -> str:
    """Codifica a lista de refs como JSON string para persistência segura."""
    return json.dumps(values or [], ensure_ascii=False)


def decode_references_json(raw_value: object) -> List[str]:
    """Desserializa a coluna JSON de refs com fallback tolerante a legado."""
    if isinstance(raw_value, list):
        return [str(value) for value in raw_value]
    if not raw_value:
        return []
    try:
        data = json.loads(str(raw_value))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(value) for value in data]


def extract_rationale_refs(content: str) -> List[RationaleRef]:
    """
    Extrai cites, wikilinks e annotations NOTE/WHY do conteúdo completo.
    """
    if not content:
        return []

    refs: List[RationaleRef] = []
    seen = set()

    def _add(ref: RationaleRef) -> None:
        key = (ref.kind, ref.key, ref.text)
        if key in seen:
            return
        seen.add(key)
        refs.append(ref)

    for match in _CITE_PATTERN.finditer(content):
        _add(
            RationaleRef(
                kind="cite",
                raw=match.group(0),
                key=_normalize_cite(match.group(1), match.group(2)),
                text=None,
            )
        )

    for match in _WIKILINK_PATTERN.finditer(content):
        key = _normalize_wikilink_key(match.group(1) or "")
        if key is None:
            continue
        _add(RationaleRef(kind="wikilink", raw=match.group(0), key=key, text=None))

    for match in _ANNOTATION_PATTERN.finditer(content):
        text = match.group(2).strip()[:200]
        if not text:
            continue
        _add(
            RationaleRef(
                kind="annotation",
                raw=match.group(0),
                key=match.group(1).strip().lower(),
                text=text,
            )
        )

    return refs
