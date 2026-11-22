import pytest

from research_mcp import server


@pytest.mark.asyncio
async def test_prompts_registered():
    names = [p.name for p in await server.mcp.list_prompts()]
    assert "systematic_literature_review" in names
    assert "draft_latex_paper" in names


def test_slr_prompt_contains_workflow_steps():
    text = server.systematic_literature_review("machine learning", 2020)
    for needle in [
        "search_papers",
        "save_paper_to_project",
        "read_paper_section",
        "verify_citations",
        "save_draft_section",
        "2020",
        "machine learning",
    ]:
        assert needle in text, f"Missing needle: {needle!r}"


def test_slr_prompt_uses_the_real_article_imrad_filenames():
    text = server.systematic_literature_review("machine learning", 2020)
    for filename in [
        "00_abstract.md",
        "01_introduction.md",
        "02_methods.md",
        "03_results.md",
        "04_discussion.md",
        "05_conclusion.md",
        "06_references.md",
    ]:
        assert filename in text, f"Missing style filename: {filename!r}"
    assert "02_methodology.md" not in text
    assert "03_findings.md" not in text
    # The prompt must point at the registry rather than claim a hardcoded outline.
    assert "get_research_style" in text
    assert "outline" in text


def test_slr_prompt_no_format_injection():
    # curly braces in topic must not raise
    text = server.systematic_literature_review("{evil}", 2020)
    assert "evil" in text


def test_slr_prompt_carries_project_id():
    text = server.systematic_literature_review("neural networks", 2019)
    assert "neural networks" in text
    assert "2019" in text


def test_slr_prompt_has_no_stray_backtick_after_the_references_note():
    text = server.systematic_literature_review("machine learning", 2020)
    assert "empty heading.`" not in text


def test_latex_prompt_uses_the_article_imrad_orders():
    text = server.draft_latex_paper("proj-3", "ieee")
    for filename, order in [
        ("00_abstract.md", 1),
        ("01_introduction.md", 2),
        ("02_methods.md", 3),
        ("03_results.md", 4),
        ("04_discussion.md", 5),
        ("05_conclusion.md", 6),
    ]:
        assert f'"{filename}", content, order={order}' in text
    assert "06_references.md" in text


def test_latex_prompt_contains_workflow_steps():
    text = server.draft_latex_paper("proj-1", "ieee")
    for needle in [
        "verify_citations",
        "export_to_latex",
        "proj-1",
        "ieee",
        "Tectonic",
    ]:
        assert needle in text, f"Missing needle: {needle!r}"


def test_latex_prompt_no_format_injection():
    # project_id with braces must not raise
    text = server.draft_latex_paper("{bad}", "ieee")
    assert "bad" in text


def test_latex_prompt_covers_imrad():
    text = server.draft_latex_paper("proj-2", "ieee")
    # Should mention IMRaD sections
    for section in ["Introduction", "Methods", "Results", "Discussion"]:
        assert section in text, f"Missing IMRaD section: {section!r}"
