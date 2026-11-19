from research_mcp.parsers.sectionizer import sectionize_markdown


def test_sectionize_splits_markdown_headings_in_order():
    sections = sectionize_markdown("# Abstract\nAbstract text.\n\n## Methods\nMethod details.")

    assert [(section.section_order, section.heading) for section in sections] == [
        (1, "Abstract"),
        (2, "Methods"),
    ]
    assert [section.content for section in sections] == [
        "Abstract text.",
        "Method details.",
    ]


def test_sectionize_keeps_preamble_as_document_section():
    sections = sectionize_markdown("Cover text.\n\n# Introduction\nBody.")

    assert [(section.heading, section.content) for section in sections] == [
        ("Document", "Cover text."),
        ("Introduction", "Body."),
    ]


def test_sectionize_returns_full_text_section_when_no_headings():
    sections = sectionize_markdown("Plain text\nwith two lines.")

    assert len(sections) == 1
    assert sections[0].heading == "Full Text"
    assert sections[0].content == "Plain text\nwith two lines."


def test_sectionize_empty_markdown_returns_empty_list():
    assert sectionize_markdown(" \n\t ") == []


def test_word_count_counts_whitespace_separated_words():
    section = sectionize_markdown("# Results\nOne  two\nthree")[0]

    assert section.word_count == 3
