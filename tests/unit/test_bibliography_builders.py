import bibtexparser

from research_mcp.citations.bibtex import (
    build_bibtex_entry,
    build_csl_entry,
    item_type_to_bibtex,
    item_type_to_csl,
)
from research_mcp.models import PaperModel


def make_paper(**overrides) -> PaperModel:
    defaults = dict(
        id="p1",
        project_id="proj",
        citekey="berg2024study",
        doi="10.1/x",
        title="A Study of Things",
        authors=["Ludwig van der Berg"],
        authors_structured=[{"given": "Ludwig", "family": "van der Berg", "literal": None}],
        year=2024,
        venue="Journal of Things",
        item_type="journal-article",
        created_at="2024-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return PaperModel(**defaults)


def test_item_type_to_bibtex_mapping():
    assert item_type_to_bibtex("journal-article") == "@article"
    assert item_type_to_bibtex("proceedings-article") == "@inproceedings"
    assert item_type_to_bibtex("book-chapter") == "@incollection"
    assert item_type_to_bibtex("preprint") == "@misc"
    assert item_type_to_bibtex("dataset") == "@misc"
    assert item_type_to_bibtex("report") == "@techreport"
    assert item_type_to_bibtex("review") == "@article"
    assert item_type_to_bibtex("other") == "@misc"


def test_item_type_to_csl_mapping():
    assert item_type_to_csl("journal-article") == "article-journal"
    assert item_type_to_csl("proceedings-article") == "paper-conference"
    assert item_type_to_csl("book-chapter") == "chapter"
    assert item_type_to_csl("preprint") == "article"
    assert item_type_to_csl("dataset") == "dataset"
    assert item_type_to_csl("report") == "report"
    assert item_type_to_csl("review") == "review"
    assert item_type_to_csl("other") == "document"


def test_build_bibtex_entry_without_raw():
    paper = make_paper()
    entry = build_bibtex_entry(paper)
    parsed = bibtexparser.parse_string(entry)
    assert len(parsed.entries) == 1
    bib = parsed.entries[0]
    assert bib.entry_type == "article"
    assert bib.key == "berg2024study"
    assert bib["title"] == "A Study of Things"
    assert bib["author"] == "van der Berg, Ludwig"
    assert bib["year"] == "2024"


def test_build_bibtex_entry_rekeys_raw():
    raw = (
        "@article{some_crossref_key,\n"
        "  author = {Doe, Jane},\n"
        "  title = {Real Publisher Title},\n"
        "  journal = {Nature},\n"
        "  pages = {1-10},\n"
        "  year = {2020},\n"
        "}\n"
    )
    paper = make_paper(bibtex_raw=raw, citekey="berg2024study")
    entry = build_bibtex_entry(paper)
    parsed = bibtexparser.parse_string(entry)
    bib = parsed.entries[0]
    assert bib.key == "berg2024study"
    # Crossref-supplied fields survive, key is replaced.
    assert bib["author"] == "Doe, Jane"
    assert bib["journal"] == "Nature"
    assert bib["pages"] == "1-10"


def test_build_csl_entry_uses_structured_author():
    paper = make_paper()
    entry = build_csl_entry(paper)
    assert entry["id"] == "berg2024study"
    assert entry["type"] == "article-journal"
    assert entry["title"] == "A Study of Things"
    assert entry["author"] == [{"given": "Ludwig", "family": "van der Berg"}]
    assert entry["DOI"] == "10.1/x"
