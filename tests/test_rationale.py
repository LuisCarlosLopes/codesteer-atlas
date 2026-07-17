from codesteer_atlas.rationale import extract_rationale_refs, serialize_rationale_refs


def test_extract_decisao_normalizes_to_dec():
    refs = extract_rationale_refs("# WHY: ver DECISAO-2")

    assert serialize_rationale_refs(refs) == ["cite:dec-002", "why:ver DECISAO-2"]


def test_extract_decisao_with_accent_and_dec_share_same_key():
    refs = extract_rationale_refs("DECISÃO-7\nDEC 7\nDECISAO_007")

    assert [ref.key for ref in refs] == ["dec-007"]


def test_extract_adr_and_rfc_preserve_prefix_with_zero_pad():
    refs = extract_rationale_refs("ADR-1\nRFC-12")

    assert serialize_rationale_refs(refs) == ["cite:adr-001", "cite:rfc-012"]


def test_extract_annotations_for_supported_comment_styles():
    content = "\n".join(
        [
            "# NOTE: cache local",
            "// WHY: reduz lookups",
            "-- NOTE: sql",
            "* WHY: markdown-ish",
        ]
    )

    refs = extract_rationale_refs(content)

    assert serialize_rationale_refs(refs) == [
        "note:cache local",
        "why:reduz lookups",
        "note:sql",
        "why:markdown-ish",
    ]


def test_annotation_text_is_truncated_to_200_chars():
    text = "a" * 240

    refs = extract_rationale_refs(f"# NOTE: {text}")

    assert len(refs[0].text) == 200


def test_extract_wikilink_reuses_markdown_pattern():
    refs = extract_rationale_refs("ver [[MCP-Server|alias]] e [[notes/mcp-index.md]]")

    assert serialize_rationale_refs(refs) == ["wikilink:mcp-server", "wikilink:mcp-index"]


def test_extract_deduplicates_repeated_refs():
    refs = extract_rationale_refs("DEC-2\nDECISÃO-002\n[[note]]\n[[note]]\n# WHY: a\n# WHY: a")

    assert serialize_rationale_refs(refs) == ["cite:dec-002", "wikilink:note", "why:a"]


def test_extract_empty_or_irrelevant_content_returns_empty():
    assert extract_rationale_refs("") == []
    assert extract_rationale_refs("sem refs aqui") == []
