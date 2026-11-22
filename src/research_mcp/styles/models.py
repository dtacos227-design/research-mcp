from typing import Literal

from pydantic import BaseModel, Field, field_validator

MARGIN_KEYS = ("top", "right", "bottom", "left")


def _default_margins() -> dict[str, float]:
    return {"top": 2.5, "right": 2.5, "bottom": 2.5, "left": 2.5}


class SectionSpec(BaseModel):
    id: str
    title: str
    level: int = 1
    filename: str
    order: int
    kind: Literal["front_matter", "body"] = "body"
    guidance: str = ""


class FormattingSpec(BaseModel):
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
    id: str
    label: str
    description: str = ""
    outline: list[SectionSpec]
    formatting: FormattingSpec = Field(default_factory=FormattingSpec)
    citation: str = "apa"
    export: dict[str, str] = Field(default_factory=dict)
    guidance: str = ""
