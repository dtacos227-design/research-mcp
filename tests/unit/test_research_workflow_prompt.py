import pytest

from research_mcp import server


@pytest.mark.asyncio
async def test_research_workflow_registered():
    names = [prompt.name for prompt in await server.mcp.list_prompts()]
    assert "research_workflow" in names


def test_research_workflow_mentions_brief_and_start():
    text = server.research_workflow_text("p1", "skripsi_s1")
    assert "save_research_brief" in text
    assert "start_research" in text
    assert "verify_citations" in text


def test_legacy_prompt_keeps_signature():
    assert "list_research_styles" in server._systematic_literature_review_text("topic", 2000)
