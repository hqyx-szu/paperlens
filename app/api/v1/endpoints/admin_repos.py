from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.repos import RepoVisibility
from app.db import get_db
from app.schemas.repo import AdminRepoPage
from app.services.admin_repos import list_admin_repos

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/repos", response_model=AdminRepoPage)
async def admin_repos_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(max_length=200)] = "",
    visibility: Annotated[RepoVisibility | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminRepoPage:
    return await list_admin_repos(
        db,
        q=q,
        visibility=visibility,
        page=page,
        page_size=page_size,
    )
