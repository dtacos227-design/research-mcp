import re

from pydantic import BaseModel


class SectionData(BaseModel):
    section_order: int
    heading: str
    content: str
    word_count: int


_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def _make_section(section_order: int, heading: str, lines: list[str]) -> SectionData:
    content = "\n".join(lines).strip()
    return SectionData(
        section_order=section_order,
        heading=heading,
        content=content,
        word_count=len(content.split()),
    )


def sectionize_markdown(markdown: str) -> list[SectionData]:
    if not markdown.strip():
        return []

    lines = markdown.splitlines()
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _ATX_HEADING.match(line)
        if match:
            headings.append((index, match.group(1).strip()))

    if not headings:
        return [_make_section(1, "Full Text", lines)]

    sections: list[SectionData] = []
    first_heading_index = headings[0][0]
    preamble = lines[:first_heading_index]
    if "\n".join(preamble).strip():
        sections.append(_make_section(1, "Document", preamble))

    for heading_index, (line_index, heading) in enumerate(headings):
        end_index = (
            headings[heading_index + 1][0] if heading_index + 1 < len(headings) else len(lines)
        )
        sections.append(
            _make_section(len(sections) + 1, heading, lines[line_index + 1 : end_index])
        )

    return sections
