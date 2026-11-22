from research_mcp.styles.models import FormattingSpec, SectionSpec, StyleSpec


def test_formatting_defaults():
    fmt = FormattingSpec()
    assert fmt.paper_size == "A4"
    assert fmt.margin_cm == {"top": 2.5, "right": 2.5, "bottom": 2.5, "left": 2.5}
    assert fmt.line_spacing == 1.5
    assert fmt.page_number_format == "decimal"


def test_style_requires_outline_sections():
    spec = StyleSpec(
        id="x",
        label="X",
        outline=[SectionSpec(id="a", title="A", filename="01_a.md", order=1)],
    )
    assert spec.outline[0].kind == "body"
    assert spec.citation == "apa"


def test_section_kind_front_matter():
    s = SectionSpec(id="cover", title="Cover", filename="00_cover.md", order=0, kind="front_matter")
    assert s.kind == "front_matter"


def test_margin_rejects_negative():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FormattingSpec(margin_cm={"top": -1, "right": 2.5, "bottom": 2.5, "left": 2.5})


def test_margin_rejects_missing_key():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FormattingSpec(margin_cm={"top": 2.5, "right": 2.5, "bottom": 2.5})
