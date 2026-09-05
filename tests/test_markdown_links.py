from codesteer_atlas.markdown_links import (
    MarkdownLinkTarget,
    extract_markdown_link_targets,
    resolve_heading_section,
    slugify_heading,
)


def test_extract_link_to_md_file_without_anchor():
    """Link simples para outro .md, sem âncora, é extraído."""
    content = "Ver também [outros docs](other.md) para detalhes."

    targets = extract_markdown_link_targets(content, "docs/index.md")

    assert targets == [MarkdownLinkTarget("docs/other.md", [], None, None)]


def test_extract_link_to_md_file_with_anchor():
    """Link para outro .md com âncora retorna o anchor separadamente."""
    content = "[ver Decisão 007](decisions.md#decisao-007)"

    targets = extract_markdown_link_targets(content, "docs/index.md")

    assert targets == [MarkdownLinkTarget("docs/decisions.md", [], "decisao-007", None)]


def test_extract_relative_path_resolved_against_source_file():
    """Path relativo com ../ é resolvido contra o diretório do arquivo de origem."""
    content = "[voltar](../README.md)"

    targets = extract_markdown_link_targets(content, "docs/specs/feature.md")

    assert targets == [MarkdownLinkTarget("docs/README.md", [], None, None)]


def test_extract_ignores_external_http_links():
    """Links externos (http/https/mailto) são ignorados."""
    content = (
        "[site](https://example.com/doc.md) "
        "[outro](http://example.com/page.md) "
        "[email](mailto:a@b.com)"
    )

    targets = extract_markdown_link_targets(content, "docs/index.md")

    assert targets == []


def test_extract_ignores_pure_anchor_links():
    """Links puramente-âncora (#secao), sem path, são ignorados."""
    content = "[ir para seção](#secao-local)"

    targets = extract_markdown_link_targets(content, "docs/index.md")

    assert targets == []


def test_extract_ignores_non_md_links():
    """Links para arquivos sem extensão .md são ignorados."""
    content = "[script](setup.sh) [config](config.json) [imagem](diagram.png)"

    targets = extract_markdown_link_targets(content, "docs/index.md")

    assert targets == []


def test_extract_deduplicates_repeated_links():
    """Links repetidos (mesmo file_path + anchor) são deduplicados."""
    content = (
        "[ver](decisions.md#decisao-007) e também [ver de novo](decisions.md#decisao-007)"
    )

    targets = extract_markdown_link_targets(content, "docs/index.md")

    assert targets == [MarkdownLinkTarget("docs/decisions.md", [], "decisao-007", None)]


def test_slugify_heading_normalizes_accents_spaces_case():
    """slugify_heading remove acentos, normaliza espaços/case e pontuação."""
    assert slugify_heading("Decisão 007: Índice Local") == "decisao-007-indice-local"
    assert slugify_heading("  Múltiplos   Espaços  ") == "multiplos-espacos"
    assert slugify_heading("Já_com_Underscore") == "ja-com-underscore"


def test_wikilink_simple_resolves_via_name_to_paths_single_match():
    """Wikilink simples [[mcp-server]] com 1 match em name_to_paths resolve file_path."""
    content = "Ver [[mcp-server]] para detalhes."
    name_to_paths = {"mcp-server": ["docs/mcp-server.md"]}

    targets = extract_markdown_link_targets(content, "docs/index.md", name_to_paths=name_to_paths)

    assert targets == [MarkdownLinkTarget("docs/mcp-server.md", [], None, None)]


def test_wikilink_ambiguous_returns_candidates():
    """Wikilink [[mcp-server]] com 2+ matches retorna file_path=None e candidates com todos os paths."""
    content = "[[mcp-server]]"
    name_to_paths = {"mcp-server": ["docs/a/mcp-server.md", "docs/b/mcp-server.md"]}

    targets = extract_markdown_link_targets(content, "docs/index.md", name_to_paths=name_to_paths)

    assert targets == [
        MarkdownLinkTarget(
            None, ["docs/a/mcp-server.md", "docs/b/mcp-server.md"], None, None
        )
    ]


def test_wikilink_without_match_returns_none_and_empty_candidates():
    """Wikilink [[mcp-server]] sem nenhum match em name_to_paths retorna file_path=None, candidates=[]."""
    content = "[[mcp-server]]"

    targets = extract_markdown_link_targets(content, "docs/index.md", name_to_paths={})

    assert targets == [MarkdownLinkTarget(None, [], None, None)]


def test_wikilink_with_alias():
    """Wikilink com alias [[mcp-server|MCP Server]] preenche o campo alias."""
    content = "[[mcp-server|MCP Server]]"
    name_to_paths = {"mcp-server": ["docs/mcp-server.md"]}

    targets = extract_markdown_link_targets(content, "docs/index.md", name_to_paths=name_to_paths)

    assert targets == [MarkdownLinkTarget("docs/mcp-server.md", [], None, "MCP Server")]


def test_wikilink_with_anchor_resolves_when_match_exists():
    """Wikilink com âncora [[mcp-server#Secao]] preenche anchor e resolve file_path se houver match."""
    content = "[[mcp-server#Secao]]"
    name_to_paths = {"mcp-server": ["docs/mcp-server.md"]}

    targets = extract_markdown_link_targets(content, "docs/index.md", name_to_paths=name_to_paths)

    assert targets == [MarkdownLinkTarget("docs/mcp-server.md", [], "Secao", None)]


def test_wikilink_explicit_relative_path_resolved_via_posixpath():
    """Wikilink com path relativo explícito [[../mindmap|alias]] é resolvido via posixpath, .md adicionado."""
    content = "[[../mindmap|alias]]"

    targets = extract_markdown_link_targets(content, "docs/specs/feature.md", name_to_paths={})

    assert targets == [MarkdownLinkTarget("docs/mindmap.md", [], None, "alias")]


def test_wikilink_pure_anchor_is_ignored():
    """Wikilink de âncora pura [[#secao-local]] é ignorado."""
    content = "[[#secao-local]]"

    targets = extract_markdown_link_targets(content, "docs/index.md", name_to_paths={})

    assert targets == []


def test_wikilink_non_md_extension_is_ignored():
    """Wikilink para extensão não-.md [[diagrama.png]] é ignorado."""
    content = "[[diagrama.png]]"

    targets = extract_markdown_link_targets(content, "docs/index.md", name_to_paths={})

    assert targets == []


def test_wikilink_vault_path_resolves_from_indexed_suffix_not_source_dir():
    """[[meta/glossary]] resolve pelo sufixo indexado, não pelo diretório da origem."""
    name_to_paths = {"glossary": ["cognitive-base/meta/glossary.md"]}
    sources = (
        "cognitive-base/system/sys-008.md",
        "cognitive-base/specs/spc-005.md",
        "cognitive-base/guides/architecture/gd-041.md",
        "cognitive-base/meta/glossary.md",
    )

    for source in sources:
        targets = extract_markdown_link_targets(
            "[[meta/glossary#crudservice]]", source, name_to_paths=name_to_paths
        )
        assert targets == [
            MarkdownLinkTarget(
                "cognitive-base/meta/glossary.md", [], "crudservice", None
            )
        ], source


def test_wikilink_vault_path_without_index_is_unresolved():
    """Sem match no índice, [[meta/glossary]] não inventa path relativo à origem."""
    targets = extract_markdown_link_targets(
        "[[meta/glossary#crudservice]]",
        "cognitive-base/system/sys-008.md",
        name_to_paths={},
    )

    assert targets == [MarkdownLinkTarget(None, [], "crudservice", None)]


def test_wikilink_vault_path_falls_back_to_basename_when_suffix_missing():
    """Se o sufixo pasta/nota não existe, cai no lookup por basename."""
    name_to_paths = {"glossary": ["docs/glossary.md"]}

    targets = extract_markdown_link_targets(
        "[[meta/glossary]]",
        "cognitive-base/system/sys-008.md",
        name_to_paths=name_to_paths,
    )

    assert targets == [MarkdownLinkTarget("docs/glossary.md", [], None, None)]


def test_wikilink_vault_path_ambiguous_suffix_returns_candidates():
    """Dois arquivos com o mesmo sufixo de vault deixam file_path vazio e candidates."""
    name_to_paths = {
        "glossary": [
            "cognitive-base/meta/glossary.md",
            "other-vault/meta/glossary.md",
        ]
    }

    targets = extract_markdown_link_targets(
        "[[meta/glossary]]",
        "cognitive-base/guides/note.md",
        name_to_paths=name_to_paths,
    )

    assert targets == [
        MarkdownLinkTarget(
            None,
            ["cognitive-base/meta/glossary.md", "other-vault/meta/glossary.md"],
            None,
            None,
        )
    ]


def test_wikilink_leading_slash_is_vault_root_not_fs_absolute():
    """[[/meta/glossary]] é raiz da vault, não path absoluto do filesystem."""
    name_to_paths = {"glossary": ["cognitive-base/meta/glossary.md"]}

    targets = extract_markdown_link_targets(
        "[[/meta/glossary]]",
        "cognitive-base/system/sys-008.md",
        name_to_paths=name_to_paths,
    )

    assert targets == [MarkdownLinkTarget("cognitive-base/meta/glossary.md", [], None, None)]


def test_resolve_heading_section_matches_github_and_obsidian_slug():
    heading = "Multi-tenancy: Master DB vs Tenant DB"
    assert (
        resolve_heading_section("multi-tenancy-master-db-vs-tenant-db", [heading])
        == heading
    )
    assert (
        resolve_heading_section(
            heading, [{"scope_name": heading}, {"scope_name": "Outro"}]
        )
        == heading
    )
    assert resolve_heading_section("inexistente", [heading]) is None


def test_chunk_with_standard_link_and_wikilink_both_present_without_duplication():
    """Chunk com link markdown padrão e wikilink retorna ambos, sem duplicação."""
    content = "[outros docs](other.md) e também [[mcp-server]]"
    name_to_paths = {"mcp-server": ["docs/mcp-server.md"]}

    targets = extract_markdown_link_targets(content, "docs/index.md", name_to_paths=name_to_paths)

    assert targets == [
        MarkdownLinkTarget("docs/other.md", [], None, None),
        MarkdownLinkTarget("docs/mcp-server.md", [], None, None),
    ]
