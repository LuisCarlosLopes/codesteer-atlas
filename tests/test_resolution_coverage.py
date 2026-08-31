"""
Regressão de cobertura de resolução (§3.3 / DECISÃO-005).

Existe por um modo de falha nomeado no roadmap: uma extensão nova entra em
`SUPPORTED_EXTENSIONS` e ninguém decide se ela tem resolver. O índice passa a
indexar aquela linguagem e, sem estes testes, se apresenta como se a resolvesse.
"""

from codesteer_atlas.config import IMPORT_RESOLUTION_TIERS, SUPPORTED_EXTENSIONS
from codesteer_atlas.graph import _IMPORT_RESOLVERS, _build_resolution_coverage

_TIERS = ("scip", "treesitter", "none")


def test_toda_linguagem_suportada_aparece_em_algum_tier():
    languages = set(SUPPORTED_EXTENSIONS.values())
    classified = set().union(*(IMPORT_RESOLUTION_TIERS[tier] for tier in _TIERS))

    assert languages - classified == set(), (
        "linguagem indexável sem tier de resolução declarado: "
        f"{sorted(languages - classified)}"
    )
    assert classified - languages == set(), (
        f"tier declara linguagem que não é indexável: {sorted(classified - languages)}"
    )


def test_nenhuma_linguagem_aparece_em_dois_tiers():
    for first in _TIERS:
        for second in _TIERS:
            if first >= second:
                continue
            overlap = IMPORT_RESOLUTION_TIERS[first] & IMPORT_RESOLUTION_TIERS[second]
            assert overlap == frozenset(), f"{first} e {second} compartilham {sorted(overlap)}"


def test_tier_treesitter_e_exatamente_o_conjunto_com_resolver():
    """
    A tabela de tiers e o registry de resolvers são duas listas que precisam concordar:
    um resolver novo sem tier faz o status mentir para menos, e um tier sem resolver
    mente para mais.
    """
    resolver_languages = {
        SUPPORTED_EXTENSIONS[extension] for extension in _IMPORT_RESOLVERS if extension in SUPPORTED_EXTENSIONS
    }

    assert resolver_languages == set(IMPORT_RESOLUTION_TIERS["treesitter"])


def test_extensao_nova_sem_tier_quebra_a_regressao(monkeypatch):
    """Prova que o guarda dispara: a checagem falha com uma extensão sem tier."""
    monkeypatch.setitem(SUPPORTED_EXTENSIONS, ".zig", "zig")

    languages = set(SUPPORTED_EXTENSIONS.values())
    classified = set().union(*(IMPORT_RESOLUTION_TIERS[tier] for tier in _TIERS))

    assert languages - classified == {"zig"}


def test_coverage_lista_so_linguagens_presentes_e_sem_duplicata():
    coverage = _build_resolution_coverage(["go", "java", "yaml", "markdown"], files_unresolved=7)

    assert coverage["treesitter"] == ["go", "java"]
    assert coverage["none"] == ["markdown", "yaml"]
    assert coverage["scip"] == []
    assert coverage["files_unresolved"] == 7
    todos = coverage["scip"] + coverage["treesitter"] + coverage["none"]
    assert len(todos) == len(set(todos))
