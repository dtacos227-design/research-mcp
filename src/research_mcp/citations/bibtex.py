import re
import unicodedata

import bibtexparser

from research_mcp.models import PaperModel


def _ascii_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", ascii_value)


def _letter_suffix(index: int) -> str:
    suffix = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        suffix = chr(ord("a") + remainder) + suffix
    return suffix


def generate_citekey(
    authors: list[str],
    year: int | None,
    title: str,
    existing: set[str] | None = None,
) -> str:
    author_token = authors[0].split()[-1] if authors and authors[0].split() else ""
    author = "".join(_ascii_words(author_token)) or "unknown"
    year_part = f"{year:04d}" if year is not None and 0 <= year <= 9999 else "0000"
    title_words = _ascii_words(title)
    title_word = title_words[0] if title_words else "paper"
    base = f"{author}{year_part}{title_word}"

    existing_keys = existing or set()
    if base not in existing_keys:
        return base

    suffix_index = 1
    while f"{base}{_letter_suffix(suffix_index)}" in existing_keys:
        suffix_index += 1
    return f"{base}{_letter_suffix(suffix_index)}"


_ITEM_TYPE_TO_BIBTEX = {
    "journal-article": "@article",
    "proceedings-article": "@inproceedings",
    "book-chapter": "@incollection",
    "preprint": "@misc",
    "dataset": "@misc",
    "report": "@techreport",
    "review": "@article",
    "other": "@misc",
}

_ITEM_TYPE_TO_CSL = {
    "journal-article": "article-journal",
    "proceedings-article": "paper-conference",
    "book-chapter": "chapter",
    "preprint": "article",
    "dataset": "dataset",
    "report": "report",
    "review": "review",
    "other": "document",
}


def item_type_to_bibtex(item_type: str) -> str:
    return _ITEM_TYPE_TO_BIBTEX.get(item_type, "@misc")


def item_type_to_csl(item_type: str) -> str:
    return _ITEM_TYPE_TO_CSL.get(item_type, "document")


def _escape_bibtex_field(value: str) -> str:
    """Strip literal braces (they would unbalance the field) and re-wrap once."""
    return "{" + value.replace("{", "").replace("}", "") + "}"


def _render_bibtex(entry_type_name: str, key: str, fields: list[tuple[str, str]]) -> str:
    """Deterministic single-outer-brace `.bib` rendering (spec §4).

    Not bibtexparser's writer: its 2.x writer re-wraps an already-braced value
    into doubled `{{...}}`, which is valid but not the single-brace form this
    design pins. bibtexparser is still used for *parsing* raw Crossref blobs.
    """
    lines = [f"@{entry_type_name}{{{key},"]
    for name, value in fields:
        lines.append(f"\t{name} = {value},")
    lines.append("}")
    return "\n".join(lines)


def _split_name(flat: str) -> tuple[str | None, str | None, str | None]:
    parts = flat.split()
    if not parts:
        return None, None, None
    if len(parts) == 1:
        return None, None, parts[0]
    return " ".join(parts[:-1]), parts[-1], None


def _bibtex_author(paper: PaperModel) -> str:
    if paper.authors_structured:
        names = []
        for author in paper.authors_structured:
            if author.get("literal"):
                names.append(author["literal"])
            elif author.get("family"):
                given = author.get("given") or ""
                names.append(f"{author['family']}, {given}".strip(", "))
            else:
                names.append(author.get("given") or "")
        return " and ".join(names)
    flat_names = []
    for name in paper.authors:
        given, family, literal = _split_name(name)
        if literal:
            flat_names.append(literal)
        else:
            flat_names.append(f"{family}, {given}".strip(", "))
    return " and ".join(flat_names)


def build_bibtex_entry(paper: PaperModel) -> str:
    """Build one `.bib` entry keyed by the paper's citekey.

    When the paper has a raw Crossref BibTeX blob, re-key that entry (keeping
    its richer publisher-supplied fields verbatim); only the entry ID is
    rewritten to our citekey. Otherwise build from structured fields.
    """
    entry_type = item_type_to_bibtex(paper.item_type)
    entry_type_name = entry_type[1:]  # strip leading '@'

    if paper.bibtex_raw:
        library = bibtexparser.parse_string(paper.bibtex_raw)
        if library.entries:
            raw_entry = library.entries[0]
            fields = [(field.key, _escape_bibtex_field(field.value)) for field in raw_entry.fields]
            # Our citekey replaces the raw entry's internal key.
            return _render_bibtex(entry_type_name, paper.citekey, fields)

    fields = [("title", _escape_bibtex_field(paper.title or ""))]
    if paper.authors:
        fields.append(("author", _escape_bibtex_field(_bibtex_author(paper))))
    if paper.year is not None:
        fields.append(("year", _escape_bibtex_field(str(paper.year))))
    if paper.venue:
        fields.append(("journal", _escape_bibtex_field(paper.venue)))
    if paper.doi:
        fields.append(("doi", _escape_bibtex_field(paper.doi)))

    return _render_bibtex(entry_type_name, paper.citekey, fields)


def _csl_author(paper: PaperModel) -> list[dict]:
    if paper.authors_structured:
        authors = []
        for author in paper.authors_structured:
            if author.get("literal"):
                authors.append({"literal": author["literal"]})
            elif author.get("family"):
                item = {"family": author["family"]}
                if author.get("given"):
                    item["given"] = author["given"]
                authors.append(item)
            elif author.get("given"):
                authors.append({"literal": author["given"]})
        return authors
    authors = []
    for name in paper.authors:
        given, family, literal = _split_name(name)
        if literal:
            authors.append({"literal": literal})
        elif family:
            item = {"family": family}
            if given:
                item["given"] = given
            authors.append(item)
    return authors


def build_csl_entry(paper: PaperModel) -> dict:
    entry: dict = {
        "id": paper.citekey,
        "type": item_type_to_csl(paper.item_type),
        "title": paper.title or "",
    }
    authors = _csl_author(paper)
    if authors:
        entry["author"] = authors
    if paper.year is not None:
        entry["issued"] = {"date-parts": [[paper.year]]}
    if paper.venue:
        entry["container-title"] = paper.venue
    if paper.doi:
        entry["DOI"] = paper.doi
    return entry
