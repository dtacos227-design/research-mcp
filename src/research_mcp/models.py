from typing import Literal

from pydantic import BaseModel, Field


class ProjectModel(BaseModel):
    id: str
    slug: str
    name: str
    description: str = ""
    style_id: str | None = None
    created_at: str
    updated_at: str


class PaperModel(BaseModel):
    id: str
    project_id: str
    citekey: str
    doi: str | None = None
    title: str
    authors: list[str]
    authors_structured: list[dict] | None = None
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    citation_count: int = 0
    pdf_path: str | None = None
    source_provider: str | None = None
    external_id: str | None = None
    item_type: str = "other"
    bibtex_raw: str | None = None
    csl_json: str | None = None
    created_at: str


class PaperSectionModel(BaseModel):
    id: int | None = None
    paper_id: str
    section_order: int
    heading: str
    content: str
    word_count: int = 0


class DraftSectionModel(BaseModel):
    id: int | None = None
    project_id: str
    filename: str
    section_order: int
    title: str | None = None
    word_count: int = 0
    updated_at: str


HandoffMode = Literal["live_authoring", "seed_docx", "batch_script"]


class HandoffPayload(BaseModel):
    project_id: str
    mode: HandoffMode
    csl_style: str = "apa"
    blueprint: dict | None = None
    handoff_markdown_path: str | None = None
    seed_docx_path: str | None = None
    script_path: str | None = None
    assets: list[str] = Field(default_factory=list)
    tool_sequence: list[dict] = Field(default_factory=list)
    run_command: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


ExportFormat = Literal["docx", "latex", "pdf", "markdown"]


class ExportResult(BaseModel):
    success: bool
    format: ExportFormat
    output_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


BriefLanguage = Literal["id", "en", "bilingual"]

BRIEF_REQUIRED_FIELDS: tuple[str, ...] = (
    "style_id",
    "title",
    "research_question",
    "citation",
)


class ResearchBrief(BaseModel):
    style_id: str | None = None
    title: str | None = None
    research_question: str | None = None
    citation: str | None = None
    language: BriefLanguage = "id"
    scope: str = ""
    target_length: str = ""
    deadline: str = ""

    def missing(self) -> list[str]:
        return [field for field in BRIEF_REQUIRED_FIELDS if not getattr(self, field)]

    def complete(self) -> bool:
        return not self.missing()
