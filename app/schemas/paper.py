from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.ai.prompts import Citation
from app.core.papers import PaperStatus
from app.schemas.common import UTCDatetime


class SummaryLevel(StrEnum):
    brief = "brief"
    standard = "standard"
    deep = "deep"


class PaperBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    is_archived: bool


class PaperDetail(BaseModel):
    id: int
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    abstract: str | None
    doi: str | None
    is_archived: bool
    has_file: bool
    can_download: bool
    can_chat: bool
    available_summary_levels: list[SummaryLevel]
    created_at: UTCDatetime


class PaperPage(BaseModel):
    items: list[PaperBrief]
    total: int
    page: int
    page_size: int


class SummaryContent(BaseModel):
    problem: str
    prior_work: str
    method_innovation: str
    limitations: str


class SummaryGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: SummaryLevel = SummaryLevel.standard
    # force=True 跳过缓存读取、重新生成并覆盖同一条缓存（契约 §8.5）。
    force: bool = False


class SummaryRead(BaseModel):
    paper_id: int
    level: SummaryLevel
    content: SummaryContent
    citations: list[Citation]
    generated_at: UTCDatetime


class PaperFileRead(BaseModel):
    original_filename: str
    size: int
    mime: str
    sha256: str
    uploaded_at: UTCDatetime


class AdminPaperListItem(BaseModel):
    id: int
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    status: PaperStatus
    is_archived: bool
    file_name: str | None
    error_message: str | None
    created_at: UTCDatetime
    updated_at: UTCDatetime


class AdminPaperDetail(AdminPaperListItem):
    abstract: str | None
    doi: str | None
    chunk_count: int
    archived_at: UTCDatetime | None
    file: PaperFileRead | None


class AdminPaperPage(BaseModel):
    items: list[AdminPaperListItem]
    total: int
    page: int
    page_size: int


class ImportItem(BaseModel):
    filename: str
    paper_id: int | None
    status: PaperStatus | None
    duplicate: bool
    is_archived: bool
    error: str | None


class ImportResponse(BaseModel):
    items: list[ImportItem]
    created: int
    duplicates: int
    rejected: int


class AdminPaperUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    authors: list[str] | None = Field(default=None, max_length=50)
    year: int | None = Field(default=None, ge=1900, le=2100)
    venue: str | None = Field(default=None, max_length=255)
    abstract: str | None = Field(default=None, max_length=20000)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("title 不能为 null")
        value = value.strip()
        if not value:
            raise ValueError("title 不能为空")
        return value

    @field_validator("authors")
    @classmethod
    def _validate_authors(cls, value: list[str] | None) -> list[str]:
        if value is None:
            raise ValueError("authors 不能为 null")
        result: list[str] = []
        seen: set[str] = set()
        for raw_name in value:
            name = raw_name.strip()
            if not 1 <= len(name) <= 200:
                raise ValueError("作者名长度必须为 1–200 字符")
            if name in seen:
                continue
            seen.add(name)
            result.append(name)
        return result

    @field_validator("venue", "abstract")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None
