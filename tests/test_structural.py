"""
Testes puros de identificação de nó, predicado de ruído e spreading activation.

Sem índice, sem LanceDB: o grafo é um dicionário sintético no padrão de
`graph._attach_query_indexes` (`_nodes_by_id`, `_adjacency`).
"""

from types import SimpleNamespace

from codesteer_atlas.config import STRUCTURAL_HUB_DEGREE_CEILING
from codesteer_atlas.structural import is_noise_hub, node_id_for, spreading_activation


def _graph(nodes, edges):
    """Monta os índices de consulta que `load_graph` anexaria."""
    adjacency = {}
    for source, target in edges:
        adjacency.setdefault(source, []).append((target, "relates"))
        adjacency.setdefault(target, []).append((source, "relates"))
    nodes_by_id = {node["id"]: node for node in nodes}
    return {"nodes": nodes, "edges": edges, "_nodes_by_id": nodes_by_id, "_adjacency": adjacency}


def test_node_id_for_usa_prefixo_sym_para_codigo_e_sec_para_markdown():
    codigo = SimpleNamespace(
        file_path="src/storage.py", scope_name="search_hybrid", language="python"
    )
    markdown = {
        "file_path": "docs/ROADMAP.md",
        "scope_name": "F2",
        "language": "markdown",
    }

    assert node_id_for(codigo) == "sym:src/storage.py#search_hybrid"
    assert node_id_for(markdown) == "sec:docs/ROADMAP.md#F2"


def test_is_noise_hub_marca_rotulo_da_denylist_e_no_acima_do_teto_de_grau():
    assert is_noise_hub({"id": "file:src/utils.py", "label": "utils.py", "degree": 2})
    teto = {
        "id": "sym:src/hub.py#Hub",
        "label": "Hub",
        "degree": STRUCTURAL_HUB_DEGREE_CEILING + 1,
    }
    assert is_noise_hub(teto)
    limpo = {"id": "sym:src/a.py#fn", "label": "fn", "degree": 3}
    assert not is_noise_hub(limpo)


def test_spreading_activation_decai_o_peso_a_cada_hop():
    seed, hop1, hop2 = "n0", "n1", "n2"
    graph = _graph(
        [
            {"id": seed, "label": "s", "degree": 1},
            {"id": hop1, "label": "a", "degree": 1},
            {"id": hop2, "label": "b", "degree": 1},
        ],
        [(seed, hop1), (hop1, hop2)],
    )

    ranked = spreading_activation(graph, [seed], {seed, hop1, hop2})

    assert ranked[0] == seed
    assert ranked.index(hop1) < ranked.index(hop2)


def test_spreading_activation_nao_expande_atraves_de_no_de_ruido():
    seed, ruido, alem = "n0", "utils", "n2"
    graph = _graph(
        [
            {"id": seed, "label": "s", "degree": 1},
            {"id": ruido, "label": "utils.py", "degree": 2},
            {"id": alem, "label": "alvo", "degree": 1},
        ],
        [(seed, ruido), (ruido, alem)],
    )

    ranked = spreading_activation(graph, [seed], {seed, ruido, alem})

    assert seed in ranked
    assert ruido in ranked
    assert alem not in ranked


def test_spreading_activation_respeita_teto_de_vizinhos_e_maximo_de_hops():
    seed = "seed"
    vizinhos = [f"v{i:02d}" for i in range(30)]
    alem_do_hop = "longe"
    hop3 = "ainda_mais_longe"
    nodes = [{"id": seed, "label": "s", "degree": 30}]
    nodes.extend({"id": vid, "label": vid, "degree": 1} for vid in vizinhos)
    nodes.append({"id": alem_do_hop, "label": "x", "degree": 1})
    nodes.append({"id": hop3, "label": "y", "degree": 1})
    edges = [(seed, vid) for vid in vizinhos]
    primeiro = sorted(vizinhos)[0]
    edges.append((primeiro, alem_do_hop))
    edges.append((alem_do_hop, hop3))
    graph = _graph(nodes, edges)

    ranked = spreading_activation(graph, [seed], {n["id"] for n in nodes})

    assert seed in ranked
    assert len([nid for nid in ranked if nid in vizinhos]) <= 25
    assert alem_do_hop in ranked
    assert hop3 not in ranked


def test_spreading_activation_so_devolve_nos_do_pool():
    seed, vizinho, fora = "n0", "n1", "n2"
    graph = _graph(
        [
            {"id": seed, "label": "s", "degree": 1},
            {"id": vizinho, "label": "a", "degree": 1},
            {"id": fora, "label": "b", "degree": 1},
        ],
        [(seed, vizinho), (vizinho, fora)],
    )

    ranked = spreading_activation(graph, [seed], {seed, vizinho})

    assert fora not in ranked
    assert set(ranked) <= {seed, vizinho}
