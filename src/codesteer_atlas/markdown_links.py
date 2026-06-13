"""Helper de extração e normalização de links Markdown [F].

Módulo independente usado por `atlas_search` para enriquecer resultados
`language=="markdown"` com referências a outros arquivos `.md` detectadas
no conteúdo do chunk, sem necessidade de reindex ou alteração de schema.
"""

import posixpath
import re
import unicodedata
from typing import List, Optional, Tuple

# @MindContext: Regex de link markdown padrão [texto](destino), reaproveitando
# o estilo de regex de cabeçalhos visto em chunker.py::_chunk_markdown.
_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

# Schemes externos ignorados — não são referências a arquivos do repositório.
_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


def extract_markdown_link_targets(
    content: str, source_file_path: str
) -> List[Tuple[str, Optional[str]]]:
    """
    Extrai links para outros arquivos `.md` referenciados em `content`.

    Ignora links externos (`http(s)://`, `mailto:`), links puramente-âncora
    (`#secao`, sem path) e links para arquivos sem extensão `.md`. Paths
    relativos (incluindo `../`) são resolvidos contra `source_file_path`.
    O resultado é deduplicado por (file_path, anchor).

    Retorna lista de tuplas `(resolved_file_path, anchor_ou_None)`.
    """
    if not content:
        return []

    source_dir = posixpath.dirname(source_file_path)

    seen = set()
    targets: List[Tuple[str, Optional[str]]] = []

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

        key = (resolved_path, anchor)
        if key in seen:
            continue
        seen.add(key)
        targets.append((resolved_path, anchor))

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
