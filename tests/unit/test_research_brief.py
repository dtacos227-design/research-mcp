from research_mcp.models import ResearchBrief


def test_missing_required_fields():
    b = ResearchBrief()
    assert b.missing() == ["style_id", "title", "research_question", "citation"]


def test_citation_defaults_to_none_but_counts_when_given_style():
    b = ResearchBrief(style_id="skripsi_s1", title="T", research_question="Q")
    assert "citation" in b.missing()
    b2 = b.model_copy(update={"citation": "apa"})
    assert b2.complete() is True


def test_language_default_is_indonesian():
    assert ResearchBrief().language == "id"
