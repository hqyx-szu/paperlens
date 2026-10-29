from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.repos import RepoPaperAddReason, RepoVisibility
from app.schemas.common import UserBrief, UTCDatetime
from app.schemas.paper import PaperBrief


class RepoPermissions(BaseModel):
    can_edit: bool
    can_delete: bool
    can_manage_papers: bool
    can_star: bool
    can_chat: bool


class RepoSummary(BaseModel):
    id: int
    name: str
    description: str
    visibility: RepoVisibility
    owner: UserBrief
    paper_count: int
    star_count: int
    is_starred: bool
    created_at: UTCDatetime
    updated_at: UTCDatetime


class RepoDetail(RepoSummary):
    permissions: RepoPermissions


class RepoPage(BaseModel):
    items: list[RepoSummary]
    total: int
    page: int
    page_size: int


class RepoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    visibility: RepoVisibility = RepoVisibility.private

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("仓库名称不能为空")
        return stripped

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RepoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    visibility: RepoVisibility | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: object) -> object:
        if value is None:
            raise ValueError("name 不能为 null")
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("name 不能为空")
        return stripped

    @field_validator("description", mode="before")
    @classmethod
    def _validate_description(cls, value: object) -> object:
        if value is None:
            raise ValueError("description 不能为 null")
        return value.strip() if isinstance(value, str) else value

    @field_validator("visibility", mode="before")
    @classmethod
    def _validate_visibility(cls, value: object) -> object:
        if value is None:
            raise ValueError("visibility 不能为 null")
        return value


class IgnoredPaper(BaseModel):
    paper_id: int
    reason: RepoPaperAddReason


class RepoPaperAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("paper_ids")
    @classmethod
    def _positive_ids(cls, value: list[int]) -> list[int]:
        if any(paper_id < 1 for paper_id in value):
            raise ValueError("paper_ids 必须是正整数")
        return value


class RepoPaperAddResult(BaseModel):
    added: list[PaperBrief]
    ignored: list[IgnoredPaper]


class StarResult(BaseModel):
    starred: bool
    star_count: int


class RelatedRepoItem(BaseModel):
    id: int
    name: str
    description: str
    owner: UserBrief
    paper_count: int
    star_count: int
    is_starred: bool
    reason: str = Field(max_length=100)


class RelatedRepoPage(BaseModel):
    items: list[RelatedRepoItem]
    total: int
    page: int
    page_size: int


class ConversationItem(BaseModel):
    id: int
    user: UserBrief
    message_count: int
    last_message_at: UTCDatetime | None
    created_at: UTCDatetime


class ConversationPage(BaseModel):
    items: list[ConversationItem]
    total: int
    page: int
    page_size: int


class AdminRepoListItem(BaseModel):
    id: int
    name: str
    visibility: RepoVisibility
    owner: UserBrief
    owner_email: str
    paper_count: int
    star_count: int
    updated_at: UTCDatetime


class AdminRepoPage(BaseModel):
    items: list[AdminRepoListItem]
    total: int
    page: int
    page_size: int
