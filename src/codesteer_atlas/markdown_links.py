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


def _ensure_md_wikilink_path(raw_target: str) -> Optional[str]:
    """Garante sufixo `.md`; None se a extensão explícita não for markdown."""
    if not raw_target:
        return None
    if "." in posixpath.basename(raw_target):
        if not raw_target.lower().endswith(".md"):
            return None
        return raw_target
    return raw_target + ".md"


def _indexed_md_paths(name_to_paths: Dict[str, List[str]]) -> List[str]:
    seen: set[str] = set()
    paths: List[str] = []
    for group in name_to_paths.values():
        for path in group:
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _match_indexed_path_suffix(path_part: str, indexed_paths: List[str]) -> List[str]:
    needle = path_part.lstrip("/").replace("\\", "/").lower()
    matches: List[str] = []
    for indexed in indexed_paths:
        normalized = indexed.replace("\\", "/").lower()
        if normalized == needle or normalized.endswith("/" + needle):
            matches.append(indexed)
    return sorted(matches)


def _unique_or_candidates(matches: List[str]) -> tuple[Optional[str], List[str]]:
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, sorted(matches)
    return None, []


def _lookup_stem(
    stem: str, name_to_paths: Dict[str, List[str]]
) -> tuple[Optional[str], List[str]]:
    return _unique_or_candidates(name_to_paths.get(stem.lower(), []))


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
    extensão `.md`. Links markdown padrão e wikilinks com `./` ou `../` são
    resolvidos contra `source_file_path`. Wikilinks com `/` (estilo Obsidian
    `[[pasta/nota]]`) resolvem contra os arquivos indexados — sufixo a partir
    da raiz da vault/workspace, sem concatenar no diretório da origem. Nome
    "bare" (sem `/`) e fallback de path sem sufixo usam `name_to_paths`
    (mapa stem -> lista de paths `.md`, tipicamente derivado de
    `manifest.files`): 1 match resolve `file_path`, 2+ matches preenchem
    `candidates`, 0 matches deixa ambos vazios — nunca inventa path relativo.

    O resultado é deduplicado por (file_path, tuple(candidates), anchor, alias).
    """
    if not content:
        return []

    source_dir = posixpath.dirname(source_file_path)
    name_to_paths = name_to_paths or {}
    indexed_md_paths = _indexed_md_paths(name_to_paths)

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

        if raw_target.startswith("."):
            path_part = _ensure_md_wikilink_path(raw_target)
            if path_part is None:
                continue
            resolved_path = posixpath.normpath(posixpath.join(source_dir, path_part))
            _add(
                MarkdownLinkTarget(
                    file_path=resolved_path, candidates=[], anchor=anchor, alias=alias
                )
            )
            continue

        if "/" in raw_target:
            # @MindWhy: Obsidian resolve [[pasta/nota]] a partir da raiz da vault,
            # não do diretório do arquivo atual. join(source_dir, …) inventava
            # paths inexistentes (ex.: system/meta/glossary.md).
            path_part = _ensure_md_wikilink_path(raw_target.lstrip("/"))
            if path_part is None:
                continue
            suffix_matches = _match_indexed_path_suffix(path_part, indexed_md_paths)
            if suffix_matches:
                file_path, candidates = _unique_or_candidates(suffix_matches)
            else:
                stem = posixpath.basename(path_part)
                if stem.lower().endswith(".md"):
                    stem = stem[: -len(".md")]
                file_path, candidates = _lookup_stem(stem, name_to_paths)
            _add(
                MarkdownLinkTarget(
                    file_path=file_path, candidates=candidates, anchor=anchor, alias=alias
                )
            )
            continue

        if "." in raw_target:
            if not raw_target.lower().endswith(".md"):
                continue
            stem = raw_target[: -len(".md")]
        else:
            stem = raw_target

        file_path, candidates = _lookup_stem(stem, name_to_paths)
        _add(
            MarkdownLinkTarget(
                file_path=file_path, candidates=candidates, anchor=anchor, alias=alias
            )
        )

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


def resolve_heading_section(anchor: str, sections: List[object]) -> Optional[str]:
    """
    Devolve o `scope_name` da seção cujo slug casa com `anchor`, ou None.

    `sections` aceita dicts com `scope_name` (projeção do índice) ou strings.
    """
    target_slug = slugify_heading(anchor)
    if not target_slug:
        return None

    for section in sections:
        name = section["scope_name"] if isinstance(section, dict) else section
        if slugify_heading(name) == target_slug:
            return name
    return None
