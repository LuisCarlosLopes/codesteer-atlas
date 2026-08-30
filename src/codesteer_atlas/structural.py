"""
Braço estrutural do RRF — funções puras sobre o grafo, sem I/O.

A junção chunk↔nó é `sym:{file_path}#{scope_name}` (código) ou
`sec:{file_path}#{scope_name}` (markdown). `is_noise_hub` é o ponto único do
predicado de ruído da expansão; F1 reutiliza depois nos rankings de hub
(DECISÃO-002). Mantido puro no padrão de `ranking.py`.
"""

from typing import Dict, Iterable, List, Sequence, Set

from codesteer_atlas.config import (
    GRAPH_NOISE_LABELS,
    STRUCTURAL_HOP_DECAY,
    STRUCTURAL_HUB_DEGREE_CEILING,
    STRUCTURAL_MAX_HOPS,
    STRUCTURAL_MAX_NEIGHBORS_PER_NODE,
)

_MARKDOWN_LANGUAGES = frozenset({"markdown"})


def node_id_for(result) -> str:
    """Id de nó determinístico a partir dos campos que `SearchResult` já carrega."""
    if isinstance(result, dict):
        file_path = result.get("file_path") or ""
        scope_name = result.get("scope_name") or ""
        language = result.get("language") or ""
    else:
        file_path = getattr(result, "file_path", "") or ""
        scope_name = getattr(result, "scope_name", "") or ""
        language = getattr(result, "language", "") or ""

    prefix = "sec" if language in _MARKDOWN_LANGUAGES else "sym"
    return f"{prefix}:{file_path}#{scope_name}"


def is_noise_hub(node: dict) -> bool:
    # @MindDecision: predicado só na expansão do braço; rankings de hub da F1 ficam intocados
    # @MindWhy: vive aqui e não em graph.py para storage não puxar viewer no import
    """True se o rótulo está na denylist ou o grau passa do teto — o nó segue elegível."""
    label = str(node.get("label") or "").casefold()
    if label in GRAPH_NOISE_LABELS:
        return True
    degree = node.get("degree") or 0
    return degree > STRUCTURAL_HUB_DEGREE_CEILING


def spreading_activation(
    graph: dict,
    seed_ids: Sequence[str],
    allowed_ids: Iterable[str],
) -> List[str]:
    """
    Ativa vizinhos a partir das sementes, com peso × STRUCTURAL_HOP_DECAY por hop.

    Não expande nó de ruído, corta vizinhos por nó, para em STRUCTURAL_MAX_HOPS.
    Devolve só ids em `allowed_ids` (chunks já no pool), ordenados por ativação.
    """
    if not seed_ids:
        return []

    allowed: Set[str] = set(allowed_ids)
    nodes_by_id: Dict[str, dict] = graph.get("_nodes_by_id") or {}
    adjacency: Dict[str, list] = graph.get("_adjacency") or {}

    activation: Dict[str, float] = {}
    for seed_id in seed_ids:
        activation[seed_id] = activation.get(seed_id, 0.0) + 1.0

    expanded: Set[str] = set()
    frontier: List[str] = list(dict.fromkeys(seed_ids))
    hops = 0

    while hops < STRUCTURAL_MAX_HOPS and frontier:
        next_frontier: List[str] = []
        for node_id in frontier:
            if node_id in expanded:
                continue
            expanded.add(node_id)

            node = nodes_by_id.get(node_id) or {"id": node_id}
            if is_noise_hub(node):
                continue

            neighbors = adjacency.get(node_id) or []
            neighbor_ids = sorted({neighbor for neighbor, _kind in neighbors})
            neighbor_ids = neighbor_ids[:STRUCTURAL_MAX_NEIGHBORS_PER_NODE]
            spread = activation.get(node_id, 0.0) * STRUCTURAL_HOP_DECAY
            for neighbor_id in neighbor_ids:
                activation[neighbor_id] = activation.get(neighbor_id, 0.0) + spread
                if neighbor_id not in expanded:
                    next_frontier.append(neighbor_id)

        frontier = list(dict.fromkeys(next_frontier))
        hops += 1

    ranked = sorted(activation.items(), key=lambda item: (-item[1], item[0]))
    return [node_id for node_id, _score in ranked if node_id in allowed]
