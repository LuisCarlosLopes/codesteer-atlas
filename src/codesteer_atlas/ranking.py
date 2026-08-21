"""
Preparo de query e reordenação pós-RRF — funções puras, sem LanceDB.

O RRF ordena por consenso entre os braços de busca, mas é cego a dois sinais
fortes e baratos: se os termos da query aparecem no **nome do símbolo**, e o
quão **próximos** eles estão dentro do chunk. Este módulo calcula esses sinais
e reordena o pool de candidatos antes do corte final.

Reranking por proximidade de termo e por casamento em campo de título é arte
prévia consolidada em recuperação de informação; a implementação aqui é original.

Mantido puro de propósito — sem I/O e sem dependência de storage — para que o
comportamento de ranking seja testável sem construir um índice, no mesmo padrão
de `rationale.py` e `markdown_links.py`.
"""

import re
from collections import defaultdict
from typing import Dict, List, Optional, Sequence

from codesteer_atlas.config import QUERY_STOPWORDS

# Peso do casamento no nome do símbolo. Código pesa mais que prosa: `scope_name`
# de código é o identificador em si (sinal altíssimo), enquanto em markdown é um
# heading, onde o corpo carrega mais informação que o título.
TITLE_WEIGHT_CODE = 0.6
TITLE_WEIGHT_PROSE = 0.3

# Teto e ponto de saturação do bônus de frase. O teto fica abaixo do máximo do
# bônus de proximidade (~1.0) e na faixa do bônus de título, para que frequência
# de par adjacente nunca domine sozinha a ordenação.
PHRASE_BOOST_CAP = 0.5
PHRASE_BOOST_SATURATION = 4

# Distância máxima (em caracteres) entre o início de dois termos para contarem
# como par adjacente.
ADJACENCY_SLACK = 2

# Termos menores que isso são ruído para proximidade/título.
MIN_TERM_LEN = 2



# Linguagens tratadas como prosa no peso do bônus de título.
PROSE_LANGUAGES = frozenset({"markdown", "text"})

_WHITESPACE = re.compile(r"\s+")

# Pontuação aparada das bordas de cada token antes da comparação com a stoplist.
_TOKEN_TRIM = "_-.,:;()[]{}\"'`"

# Dobra de acento 1-para-1 (nunca muda o comprimento da string, para não
# deslocar as posições usadas no cálculo de proximidade). NFKD não serve aqui:
# decompõe "í" em dois code points e desalinharia todos os offsets.
_ACCENT_SRC = "áàâãäåéèêëíìîïóòôõöúùûüçñýÿ"
_ACCENT_DST = "aaaaaaeeeeiiiiooooouuuucnyy"
_FOLD_TABLE = str.maketrans(_ACCENT_SRC, _ACCENT_DST)


def fold(text: str) -> str:
    """Normaliza para minúsculo sem acento, preservando o comprimento original."""
    return text.lower().translate(_FOLD_TABLE)


def _normalize_token(token: str) -> str:
    return fold(token).strip(_TOKEN_TRIM)





def query_terms(query: str) -> List[str]:
    """
    Termos normalizados usados pelos boosts de reordenação.

    Se a poda zerar a lista, devolve os termos originais — sem esse fallback o rerank
    viraria no-op silencioso em queries feitas só de termos genéricos.
    """
    raw = [_normalize_token(t) for t in _WHITESPACE.split(query.strip()) if t]
    raw = [t for t in raw if len(t) >= MIN_TERM_LEN]
    if not raw:
        return []

    meaningful = [t for t in raw if t not in QUERY_STOPWORDS]
    return meaningful or raw


def _all_positions(haystack: str, needle: str) -> List[int]:
    """Todos os offsets de `needle` em `haystack`, incluindo ocorrências sobrepostas."""
    positions = []
    start = haystack.find(needle)
    while start != -1:
        positions.append(start)
        start = haystack.find(needle, start + 1)
    return positions


def min_span(position_lists: Sequence[Sequence[int]]) -> Optional[int]:
    """
    Menor janela (em caracteres) que contém pelo menos uma ocorrência de cada termo.

    Retorna `None` quando algum termo não ocorre — o chamador trata isso como
    ausência de sinal de proximidade, não como proximidade máxima.
    """
    if not position_lists or any(len(p) == 0 for p in position_lists):
        return None

    events = sorted(
        (pos, term_index)
        for term_index, positions in enumerate(position_lists)
        for pos in positions
    )

    needed = len(position_lists)
    counts: Dict[int, int] = defaultdict(int)
    distinct = 0
    best: Optional[int] = None
    left = 0

    for right in range(len(events)):
        counts[events[right][1]] += 1
        if counts[events[right][1]] == 1:
            distinct += 1

        while distinct == needed:
            span = events[right][0] - events[left][0]
            if best is None or span < best:
                best = span
            counts[events[left][1]] -= 1
            if counts[events[left][1]] == 0:
                distinct -= 1
            left += 1

    return best


def title_boost(scope_name: str, terms: Sequence[str], is_code: bool) -> float:
    """Fração dos termos da query presentes no nome do símbolo, ponderada por tipo."""
    if not terms or not scope_name:
        return 0.0

    folded = fold(scope_name)
    hits = sum(1 for t in terms if t in folded)
    if hits == 0:
        return 0.0

    weight = TITLE_WEIGHT_CODE if is_code else TITLE_WEIGHT_PROSE
    return weight * (hits / len(terms))


def proximity_boost(content: str, terms: Sequence[str]) -> float:
    """
    Recompensa a menor janela que contém todos os termos, normalizada pelo tamanho
    do chunk.

    Só faz sentido com 2+ termos. Usa a janela mais apertada e ignora frequência:
    um chunk longo com uma ocorrência bem agrupada vence um chunk curto com os
    termos espalhados — repetição é premiada por `phrase_boost`, não aqui.
    """
    if len(terms) < 2 or not content:
        return 0.0

    folded = fold(content)
    span = min_span([_all_positions(folded, t) for t in terms])
    if span is None:
        return 0.0

    return 1.0 / (1.0 + span / max(len(folded), 1))


def phrase_boost(content: str, terms: Sequence[str]) -> float:
    """
    Bônus saturante por termos distintos aparecendo lado a lado.

    Satura em `PHRASE_BOOST_SATURATION` ocorrências para que um chunk que repete o
    par dezenas de vezes não domine a ordenação por volume.
    """
    if len(terms) < 2 or not content:
        return 0.0

    folded = fold(content)
    events = sorted(
        (pos, term_index)
        for term_index, term in enumerate(terms)
        for pos in _all_positions(folded, term)
    )

    adjacent = 0
    for i in range(len(events) - 1):
        pos_a, term_a = events[i]
        pos_b, term_b = events[i + 1]
        if term_a == term_b:
            continue
        if pos_b - pos_a <= len(terms[term_a]) + ADJACENCY_SLACK:
            adjacent += 1

    if adjacent == 0:
        return 0.0

    return PHRASE_BOOST_CAP * min(1.0, adjacent / PHRASE_BOOST_SATURATION)


def score_result(
    scope_name: str,
    scope_type: str,
    language: str,
    content: Optional[str],
    terms: Sequence[str],
) -> float:
    """Boost total de reordenação de um candidato. Zero quando não há termos úteis."""
    if not terms:
        return 0.0

    is_code = language not in PROSE_LANGUAGES and scope_type != "module"
    body = content or ""

    return (
        title_boost(scope_name, terms, is_code)
        + proximity_boost(body, terms)
        + phrase_boost(body, terms)
    )


def rerank(results: List, query: str) -> List:
    """
    Reordena candidatos já fundidos pelo RRF, do maior boost para o menor, usando o
    score RRF como desempate.

    Deixar o boost dominar é deliberado e foi medido contra as alternativas: ponderar
    o RRF pelo boost (`rrf * (1 + boost)`) e desligar o rerank em prosa ficaram
    ambos ATRÁS desta versão em todas as quatro classes do golden set
    (MRR total 0.565 e 0.550 contra 0.605). O casamento literal em nome de símbolo
    carrega mais sinal do que a intuição sugere, inclusive em linguagem natural.

    Recebe e devolve `SearchResult`, mas depende apenas dos atributos —
    deliberadamente não importa `models`, para manter o módulo livre de
    dependência de esquema.
    """
    terms = query_terms(query)
    if not terms or not results:
        return results

    scored = [
        (
            score_result(r.scope_name, r.scope_type, r.language, r.content, terms),
            r.score,
            index,
            r,
        )
        for index, r in enumerate(results)
    ]

    # `index` como último desempate mantém a ordenação estável: com boosts e scores
    # iguais, a saída é exatamente a ordem de entrada do RRF.
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [row[3] for row in scored]
