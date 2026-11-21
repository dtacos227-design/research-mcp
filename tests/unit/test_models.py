import pytest
from pydantic import ValidationError

from research_mcp.models import (
    DraftSectionModel,
    ExportResult,
    HandoffPayload,
    PaperModel,
    PaperSectionModel,
    ProjectModel,
)


def test_project_model_accepts_valid_data():
    project = ProjectModel(
        id="p1",
        slug="my-project",
        name="My Project",
        created_at="2026-11-20T00:00:00Z",
        updated_at="2026-11-20T00:00:00Z",
    )
    assert project.description == ""


def test_project_model_requires_name():
    with pytest.raises(ValidationError):
        ProjectModel(id="p1", slug="my-project", created_at="x", updated_at="x")


def test_paper_model_accepts_valid_data():
    paper = PaperModel(
        id="pa1",
        project_id="p1",
        citekey="vaswani2017attention",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani"],
        created_at="2026-11-20T00:00:00Z",
    )
    assert paper.citation_count == 0
    assert paper.doi is None


def test_paper_section_model_accepts_valid_data():
    section = PaperSectionModel(paper_id="pa1", section_order=1, heading="Abstract", content="...")
    assert section.word_count == 0


def test_draft_section_model_accepts_valid_data():
    draft = DraftSectionModel(
        project_id="p1",
        filename="01_abstract.md",
        section_order=1,
        updated_at="2026-11-20T00:00:00Z",
    )
    assert draft.title is None


def test_handoff_payload_rejects_invalid_mode():
    with pytest.raises(ValidationError):
        HandoffPayload(project_id="p1", mode="live_authoring_typo")


def test_handoff_payload_accepts_valid_mode():
    payload = HandoffPayload(project_id="p1", mode="seed_docx")
    assert payload.csl_style == "apa"


def test_handoff_payload_handoff_details_default_empty():
    payload = HandoffPayload(project_id="p1", mode="live_authoring")
    assert payload.handoff_markdown_path is None
    assert payload.seed_docx_path is None
    assert payload.script_path is None
    assert payload.assets == []
    assert payload.tool_sequence == []
    assert payload.run_command == []
    assert payload.warnings == []


def test_export_result_rejects_invalid_format():
    with pytest.raises(ValidationError):
        ExportResult(success=True, format="docx-typo")


def test_export_result_accepts_valid_format():
    result = ExportResult(success=True, format="docx", output_path="/tmp/out.docx")
    assert result.warnings == []
    assert result.errors == []
