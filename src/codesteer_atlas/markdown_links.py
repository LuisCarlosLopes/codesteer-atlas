"""Helper de extração e normalização de links Markdown [F].

Módulo independente usado por `atlas_search` para enriquecer resultados
`language=="markdown"` com referências a outros arquivos `.md` detectadas
no conteúdo do chunk, sem necessidade de reindex ou alteração de schema.
"""

import posixpath
import re
import unicodedata
from typing import Dict, List, NamedTuple, Optional

# @MindContext: Regex de link markdown padrão [texto](destino), reaproveitando
# o estilo de regex de cabeçalhos visto em chunker.py::_chunk_markdown.
_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

# @MindContext: Regex de wikilink do Obsidian: [[destino]], [[destino|alias]],
# [[destino#Secao]], [[destino#Secao|alias]]. Casa também `![[embed]]` pois o
# `!` anterior não faz parte do match.
_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]*)(?:#([^\]|]*))?(?:\|([^\]]*))?\]\]")

# Schemes externos ignorados — não são referências a arquivos do repositório.
_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


class MarkdownLinkTarget(NamedTuple):
    """Referência a outro arquivo `.md` extraída de um link/wikilink."""

    file_path: Optional[str]  # path resolvido (None se ambíguo ou não resolvido)
    candidates: List[str]  # candidatos quando ambíguo (lista vazia caso contrário)
    anchor: Optional[str]
    alias: Optional[str]  # alias do wikilink ([[destino|alias]]); None se ausente


def extract_markdown_link_targets(
    content: str,
    source_file_path: str,
    name_to_paths: Optional[Dict[str, List[str]]] = None,
) -> List[MarkdownLinkTarget]:
    """
    Extrai referências a outros arquivos `.md` em `content`: links markdown
    padrão (`[texto](destino.md)`) e wikilinks do Obsidian (`[[destino]]`).

    Ignora links externos (`http(s)://`, `mailto:`), referências
    puramente-âncora (`#secao`, sem destino) e referências para arquivos sem
    extensão `.md`. Paths relativos/explícitos (incluindo `../`) são
    resolvidos contra `source_file_path`. Wikilinks com nome "bare" (sem `/`
    ou `.` no início) são resolvidos globalmente via `name_to_paths`
    (mapa stem -> lista de paths `.md`, tipicamente derivado de
    `manifest.files`): 1 match resolve `file_path`, 2+ matches preenchem
    `candidates`, 0 matches deixa ambos vazios.

    O resultado é deduplicado por (file_path, tuple(candidates), anchor, alias).
    """
    if not content:
        return []

    source_dir = posixpath.dirname(source_file_path)
    name_to_paths = name_to_paths or {}

    seen = set()
    targets: List[MarkdownLinkTarget] = []

    def _add(target: MarkdownLinkTarget) -> None:
        key = (target.file_path, tuple(target.candidates), target.anchor, target.alias)
        if key in seen:
            return
        seen.add(key)
        targets.append(target)

    # Loop 1: links markdown padrão [texto](destino)
    for match in _LINK_PATTERN.finditer(content):
        destination = match.group(2).strip()

        # Ignora links externos (http/https/mailto)
        if destination.lower().startswith(_EXTERNAL_SCHEMES):
            continue

        # Ignora links puramente-âncora (sem path), ex: [texto](#secao)
        if destination.startswith("#"):
            continue

        # Separa path e âncora (#anchor), se houver
        if "#" in destination:
            path_part, anchor = destination.split("#", 1)
            anchor = anchor or None
        else:
            path_part, anchor = destination, None

        if not path_part:
            continue

        # Ignora links para arquivos sem extensão .md
        if not path_part.lower().endswith(".md"):
            continue

        # Resolve path relativo (incluindo ../) contra o diretório do arquivo de origem
        resolved_path = posixpath.normpath(posixpath.join(source_dir, path_part))

        _add(MarkdownLinkTarget(file_path=resolved_path, candidates=[], anchor=anchor, alias=None))

    # Loop 2: wikilinks do Obsidian [[destino]], [[destino|alias]], [[destino#Secao]]
    for match in _WIKILINK_PATTERN.finditer(content):
        raw_target = match.group(1).strip()
        anchor = (match.group(2) or "").strip() or None
        alias = (match.group(3) or "").strip() or None

        # Ignora âncora pura [[#Heading]] (sem destino)
        if not raw_target:
            continue

        if raw_target.startswith("/") or raw_target.startswith(".") or "/" in raw_target:
            # Path explícito: garante sufixo .md, ignora extensão != .md
            if "." in posixpath.basename(raw_target):
                if not raw_target.lower().endswith(".md"):
                    continue
                path_part = raw_target
            else:
                path_part = raw_target + ".md"

            resolved_path = posixpath.normpath(posixpath.join(source_dir, path_part))
            _add(
                MarkdownLinkTarget(
                    file_path=resolved_path, candidates=[], anchor=anchor, alias=alias
                )
            )
        else:
            # Nome "bare": resolve globalmente por stem via name_to_paths
            if "." in raw_target:
                if not raw_target.lower().endswith(".md"):
                    continue
                stem = raw_target[: -len(".md")]
            else:
                stem = raw_target

            # Lookup case-insensitive: name_to_paths usa stems em minúsculas
            matches = name_to_paths.get(stem.lower(), [])
            if len(matches) == 1:
                _add(
                    MarkdownLinkTarget(
                        file_path=matches[0], candidates=[], anchor=anchor, alias=alias
                    )
                )
            elif len(matches) > 1:
                _add(
                    MarkdownLinkTarget(
                        file_path=None, candidates=sorted(matches), anchor=anchor, alias=alias
                    )
                )
            else:
                _add(MarkdownLinkTarget(file_path=None, candidates=[], anchor=anchor, alias=alias))

    return targets


def slugify_heading(text: str) -> str:
    """
    Normaliza um cabeçalho/anchor markdown para comparação.

    Remove acentos (NFKD), converte para minúsculas, troca espaços e
    underscores por hífen, remove pontuação remanescente e colapsa
    hífens consecutivos — espelhando a normalização de slugs usada por
    renderizadores markdown comuns (ex: GitHub).
    """
    if not text:
        return ""

    # Remove acentos via normalização NFKD + filtro de combining marks
    normalized = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))

    lowered = without_accents.lower()

    # Espaços e underscores -> hífen
    with_hyphens = re.sub(r"[\s_]+", "-", lowered)

    # Remove qualquer caractere que não seja letra, número ou hífen
    cleaned = re.sub(r"[^a-z0-9-]", "", with_hyphens)

    # Colapsa hífens consecutivos e remove hífens nas extremidades
    collapsed = re.sub(r"-+", "-", cleaned).strip("-")

    return collapsed
