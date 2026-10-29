from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.repos import RepoVisibility
from app.models import Repo, RepoPaper, RepoStar, User
from app.schemas.common import UserBrief
from app.schemas.repo import AdminRepoListItem, AdminRepoPage


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_admin_repos(
    db: AsyncSession,
    *,
    q: str,
    visibility: RepoVisibility | None,
    page: int,
    page_size: int,
) -> AdminRepoPage:
    filters = []
    keyword = q.strip()
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        filters.append(
            or_(
                Repo.name.like(pattern, escape="\\"),
                User.email.like(pattern, escape="\\"),
                User.display_name.like(pattern, escape="\\"),
            )
        )
    if visibility is not None:
        filters.append(Repo.visibility == visibility.value)

    base = select(Repo.id).join(User, User.id == Repo.owner_id).where(*filters)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0

    rows = (
        await db.execute(
            select(Repo, User.id, User.display_name, User.email)
            .join(User, User.id == Repo.owner_id)
            .where(*filters)
            .order_by(Repo.updated_at.desc(), Repo.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    repo_ids = [repo.id for repo, _, _, _ in rows]
    if not repo_ids:
        return AdminRepoPage(items=[], total=total, page=page, page_size=page_size)

    paper_rows = (
        await db.execute(
            select(RepoPaper.repo_id, func.count())
            .where(RepoPaper.repo_id.in_(repo_ids))
            .group_by(RepoPaper.repo_id)
        )
    ).all()
    star_rows = (
        await db.execute(
            select(RepoStar.repo_id, func.count())
            .where(RepoStar.repo_id.in_(repo_ids))
            .group_by(RepoStar.repo_id)
        )
    ).all()
    paper_counts = {repo_id: int(count) for repo_id, count in paper_rows}
    star_counts = {repo_id: int(count) for repo_id, count in star_rows}

    items = [
        AdminRepoListItem(
            id=repo.id,
            name=repo.name,
            visibility=RepoVisibility(repo.visibility),
            owner=UserBrief(id=owner_id, display_name=display_name),
            owner_email=email,
            paper_count=paper_counts.get(repo.id, 0),
            star_count=star_counts.get(repo.id, 0)
            if repo.visibility == RepoVisibility.public.value
            else 0,
            updated_at=repo.updated_at,
        )
        for repo, owner_id, display_name, email in rows
    ]
    return AdminRepoPage(items=items, total=total, page=page, page_size=page_size)
