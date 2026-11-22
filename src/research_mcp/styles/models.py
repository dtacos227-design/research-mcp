import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MARGIN_KEYS = ("top", "right", "bottom", "left")

# Single source of truth for draft chapter file names shared with
# `research_mcp.writers.draft_manager`: flat, `.md` only, no directories.
SAFE_DRAFT_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")


def _default_margins() -> dict[str, float]:
    return {"top": 2.5, "right": 2.5, "bottom": 2.5, "left": 2.5}


class SectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    level: int = 1
    filename: str
    order: int
    kind: Literal["front_matter", "body"] = "body"
    guidance: str = ""

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        """Keep outline names addressable by draft_manager's read/write tools."""
        if not SAFE_DRAFT_FILENAME_RE.match(value) or ".." in value:
            raise ValueError(
                f"filename {value!r} must be a flat `.md` file name such as "
                "'01_introduction.md' (no directories or '..', letters/digits/._- only)."
            )
        return value


class FormattingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_size: Literal["A4", "Letter"] = "A4"
    margin_cm: dict[str, float] = Field(default_factory=_default_margins)
    line_spacing: float = 1.5
    justify: bool = True
    font_family: str = "Times New Roman"
    font_size_pt: float = 12
    heading_numbering: Literal["none", "arabic", "roman_bab", "numeric_bab"] = "arabic"
    page_number_position: Literal["footer_center", "footer_right", "header_right", "none"] = (
        "footer_center"
    )
    page_number_format: Literal["decimal", "lowerRoman", "upperRoman"] = "decimal"
    two_column: bool = False

    @field_validator("margin_cm")
    @classmethod
    def _validate_margin_cm(cls, value: dict[str, float]) -> dict[str, float]:
        missing = [key for key in MARGIN_KEYS if key not in value]
        if missing:
            raise ValueError(
                f"margin_cm must define all of {', '.join(MARGIN_KEYS)}; "
                f"missing: {', '.join(missing)}"
            )
        negative = [key for key, margin in value.items() if margin < 0]
        if negative:
            raise ValueError(
                "margin_cm values must be non-negative; "
                f"got negative values for: {', '.join(negative)}"
            )
        return value


class StyleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str = ""
    outline: list[SectionSpec]
    formatting: FormattingSpec = Field(default_factory=FormattingSpec)
    citation: str = "apa"
    export: dict[str, str] = Field(default_factory=dict)
    guidance: str = ""
    bibliography_title: str = "References"

    @model_validator(mode="after")
    def _validate_unique_outline_filenames(self) -> "StyleSpec":
        """One draft file per section: a duplicate would silently drop a chapter."""
        seen: set[str] = set()
        duplicates: list[str] = []
        for section in self.outline:
            if section.filename in seen and section.filename not in duplicates:
                duplicates.append(section.filename)
            seen.add(section.filename)
        if duplicates:
            names = ", ".join(repr(name) for name in duplicates)
            raise ValueError(f"outline filenames must be unique; duplicate filename(s): {names}")
        return self


_TOC_MARKERS = ("daftarisi", "tableofcontents", "contents")


def outline_key(text: str) -> str:
    """Loose matching key: lowercase letters and digits only."""
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def is_toc_text(text: str) -> bool:
    """True when a heading/id names a table of contents ("Daftar Isi", "Contents")."""
    key = outline_key(text)
    return any(marker in key for marker in _TOC_MARKERS)


def is_toc_section(section: SectionSpec) -> bool:
    """True when an outline section stands for an auto-generated table of contents."""
    return is_toc_text(section.title) or is_toc_text(section.id)


def style_toc_section(style: StyleSpec) -> SectionSpec | None:
    """The style's table-of-contents section, if it declares one."""
    return next((section for section in style.outline if is_toc_section(section)), None)
