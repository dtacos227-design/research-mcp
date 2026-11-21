import pytest

from research_mcp import server


@pytest.mark.asyncio
async def test_prompts_registered():
    names = [prompt.name for prompt in await server.mcp.list_prompts()]
    assert "write_in_wordcraft" in names
    assert "peer_review_in_wordcraft" in names


def test_write_prompt_mentions_safety_rules():
    text = server.write_in_wordcraft("p1")
    for needle in [
        "check_wordcraft_env",
        "prepare_wordcraft_handoff",
        "file.strictReport",
        "caret.docEnd",
        "outcome unknown",
        "inspect_document",
        "--connect",
        "review.trackChanges",
        "export_to_docx",
    ]:
        assert needle in text


def test_write_prompt_carries_parameters():
    text = server.write_in_wordcraft("proj-42", "ieee", "thesis")
    assert "proj-42" in text
    assert "ieee" in text
    assert "thesis.docx" in text


def test_peer_review_prompt_mentions_track_changes():
    text = server.peer_review_in_wordcraft("p1", "/tmp/doc.docx")
    assert 'review.trackChanges", {"value": true}' in text
    assert "search_local_library" in text
    assert "read_paper_section" in text
    assert "/tmp/doc.docx" in text
