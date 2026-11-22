import pytest

from research_mcp.styles import registry

EXPECTED = {
    "article_imrad",
    "ieee_paper",
    "skripsi_s1",
    "tesis_s2",
    "proposal_penelitian",
    "case_study",
    "rapid_synthesis",
    "annotated_bibliography",
}


def test_all_builtin_styles_present():
    ids = {s["id"] for s in registry.list_styles()}
    assert EXPECTED <= ids


@pytest.mark.parametrize("style_id", sorted(EXPECTED))
def test_style_outline_is_ordered_and_unique(style_id):
    spec = registry.get_style(style_id)
    assert spec.outline
    orders = [s.order for s in spec.outline]
    assert all(a < b for a, b in zip(orders, orders[1:], strict=False))
    filenames = [s.filename for s in spec.outline]
    assert len(filenames) == len(set(filenames))


@pytest.mark.parametrize("style_id", sorted(EXPECTED))
def test_guidance_is_bilingual(style_id):
    spec = registry.get_style(style_id)
    assert "EN:" in spec.guidance, f"{style_id} guidance lacks an EN: summary"
    for section in spec.outline:
        assert "EN:" in section.guidance, f"{style_id}/{section.id} lacks an EN: summary"


def test_ieee_paper_uses_two_column_ieee_layout():
    spec = registry.get_style("ieee_paper")
    assert spec.formatting.two_column is True
    assert spec.formatting.line_spacing == 1.0
    assert spec.citation == "ieee"


def test_skripsi_has_front_matter_and_five_bab():
    spec = registry.get_style("skripsi_s1")
    front = [s for s in spec.outline if s.kind == "front_matter"]
    body = [s for s in spec.outline if s.kind == "body"]
    assert front, "skripsi must have front matter"
    assert len(body) == 5
    assert spec.formatting.margin_cm == {"top": 3.0, "right": 3.0, "bottom": 3.0, "left": 4.0}
    assert spec.formatting.line_spacing == 2.0
