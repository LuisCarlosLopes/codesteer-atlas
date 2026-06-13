from codesteer_atlas.markdown_links import extract_markdown_link_targets, slugify_heading


def test_extract_link_to_md_file_without_anchor():
    """Link simples para outro .md, sem âncora, é extraído."""
    content = "Ver também [outros docs](other.md) para detalhes."

    targets = extract_markdown_link_targets(content, "docs/index.md")

    assert targets == [("docs/other.md", None)]


def test_extract_link_to_md_file_with_anchor():
    """Link para outro .md com âncora retorna o anchor separadamente."""
    content = "[ver Decisão 007](decisions.md#decisao-007)"

    targets = extract_markdown_link_targets(content, "docs/index.md")

    assert targets == [("docs/decisions.md", "decisao-007")]


def test_extract_relative_path_resolved_against_source_file():
    """Path relativo com ../ é resolvido contra o diretório do arquivo de origem."""
    content = "[voltar](../README.md)"

    targets = extract_markdown_link_targets(content, "docs/specs/feature.md")

    assert targets == [("docs/README.md", None)]


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

    assert targets == [("docs/decisions.md", "decisao-007")]


def test_slugify_heading_normalizes_accents_spaces_case():
    """slugify_heading remove acentos, normaliza espaços/case e pontuação."""
    assert slugify_heading("Decisão 007: Índice Local") == "decisao-007-indice-local"
    assert slugify_heading("  Múltiplos   Espaços  ") == "multiplos-espacos"
    assert slugify_heading("Já_com_Underscore") == "ja-com-underscore"
