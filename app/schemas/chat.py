from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.ai.prompts import Citation
from app.core.chat import MessageRole, MessageStatus, QueryType
from app.schemas.common import UTCDatetime
from app.schemas.paper import PaperBrief


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)

    @field_validator("query", mode="before")
    @classmethod
    def _strip_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message", mode="before")
    @classmethod
    def _strip_message(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SessionSummary(BaseModel):
    id: int
    query: str
    query_type: QueryType
    paper_count: int
    evidence_count: int
    created_at: UTCDatetime


class SessionDetail(BaseModel):
    id: int
    query: str
    query_type: QueryType
    paper_count: int
    evidence_count: int
    related_repo_count: int
    has_evidence: bool
    system_message: str
    conversation_id: int
    papers: list[PaperBrief]
    created_at: UTCDatetime


class SessionPage(BaseModel):
    items: list[SessionSummary]
    total: int
    page: int
    page_size: int


class MessageRead(BaseModel):
    id: int
    role: MessageRole
    content: str
    citations: list[Citation]
    status: MessageStatus
    created_at: UTCDatetime


class MessagePage(BaseModel):
    items: list[MessageRead]
    has_more: bool
    next_before_id: int | None
