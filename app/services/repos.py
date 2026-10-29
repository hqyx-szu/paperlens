from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chat import ChatScope
from app.core.exceptions import AppError
from app.core.papers import PaperStatus
from app.core.repos import RepoPaperAddReason, RepoScope, RepoVisibility
from app.core.roles import UserRole
from app.core.time import utcnow
from app.models import Conversation, Paper, Repo, RepoPaper, RepoStar, User
from app.schemas.common import UserBrief
from app.schemas.paper import PaperBrief, PaperPage
from app.schemas.repo import (
    IgnoredPaper,
    RepoCreate,
    RepoDetail,
    RepoPage,
    RepoPaperAddResult,
    RepoPermissions,
    RepoSummary,
    RepoUpdate,
    StarResult,
)

REPO_NOT_FOUND = "仓库不存在"


@dataclass(frozen=True, slots=True)
class RepoCounts:
    paper_count: int = 0
    star_count: int = 0
    is_starred: bool = False


def repo_permissions(repo: Repo, user: User) -> RepoPermissions:
    """仓库权限的唯一口径：接口与序列化都只调用这里。"""
    is_owner = repo.owner_id == user.id
    is_admin = user.role == UserRole.admin.value
    is_public = repo.visibility == RepoVisibility.public.value
    can_manage = is_owner or (is_admin and not is_public)
    return RepoPermissions(
        can_edit=can_manage,
        can_delete=can_manage,
        can_manage_papers=can_manage,
        can_star=is_public and not is_owner,
        can_chat=True,
    )


def is_public_repo(repo: Repo) -> bool:
    return repo.visibility == RepoVisibility.public.value


async def get_visible_repo(
    db: AsyncSession,
    *,
    repo_id: int,
    user: User,
) -> Repo:
    repo = await db.get(Repo, repo_id)
    if repo is None:
        raise AppError(404, "repo_not_found", REPO_NOT_FOUND)
    if not is_public_repo(repo) and repo.owner_id != user.id and user.role != UserRole.admin.value:
        raise AppError(404, "repo_not_found", REPO_NOT_FOUND)
    return repo


def require_repo_manager(repo: Repo, user: User) -> None:
    is_owner = repo.owner_id == user.id
    is_admin = user.role == UserRole.admin.value
    if is_owner or (is_admin and not is_public_repo(repo)):
        return
    raise AppError(403, "forbidden", "无权访问")


async def repo_counts(
    db: AsyncSession,
    *,
    repo_ids: list[int],
    user_id: int,
) -> dict[int, RepoCounts]:
    """批量统计：两个独立 group by + 一个 star 集合，避免 join 扇出放大计数。"""
    if not repo_ids:
        return {}

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
    starred_ids = set(
        (
            await db.scalars(
                select(RepoStar.repo_id).where(
                    RepoStar.user_id == user_id,
                    RepoStar.repo_id.in_(repo_ids),
                )
            )
        ).all()
    )

    paper_counts = {repo_id: int(count) for repo_id, count in paper_rows}
    star_counts = {repo_id: int(count) for repo_id, count in star_rows}
    return {
        repo_id: RepoCounts(
            paper_count=paper_counts.get(repo_id, 0),
            star_count=star_counts.get(repo_id, 0),
            is_starred=repo_id in starred_ids,
        )
        for repo_id in repo_ids
    }


async def _owner_briefs(db: AsyncSession, owner_ids: set[int]) -> dict[int, UserBrief]:
    if not owner_ids:
        return {}
    rows = (
        await db.execute(select(User.id, User.display_name).where(User.id.in_(owner_ids)))
    ).all()
    return {row[0]: UserBrief(id=row[0], display_name=row[1]) for row in rows}


async def repo_summaries(
    db: AsyncSession,
    repos: list[Repo],
    user: User,
) -> list[RepoSummary]:
    if not repos:
        return []

    counts = await repo_counts(db, repo_ids=[repo.id for repo in repos], user_id=user.id)
    owners = await _owner_briefs(db, {repo.owner_id for repo in repos})

    items: list[RepoSummary] = []
    for repo in repos:
        stats = counts.get(repo.id, RepoCounts())
        public = is_public_repo(repo)
        is_owner = repo.owner_id == user.id
        items.append(
            RepoSummary(
                id=repo.id,
                name=repo.name,
                description=repo.description,
                visibility=RepoVisibility(repo.visibility),
                owner=owners.get(
                    repo.owner_id,
                    UserBrief(id=repo.owner_id, display_name=None),
                ),
                paper_count=stats.paper_count,
                star_count=stats.star_count if public else 0,
                is_starred=stats.is_starred and public and not is_owner,
                created_at=repo.created_at,
                updated_at=repo.updated_at,
            )
        )
    return items


async def get_repo_detail(db: AsyncSession, repo: Repo, user: User) -> RepoDetail:
    summary = (await repo_summaries(db, [repo], user))[0]
    return RepoDetail(**summary.model_dump(), permissions=repo_permissions(repo, user))


async def list_repos(
    db: AsyncSession,
    *,
    user: User,
    scope: RepoScope,
    page: int,
    page_size: int,
) -> RepoPage:
    condition = (
        Repo.owner_id == user.id
        if scope is RepoScope.mine
        else Repo.visibility == RepoVisibility.public.value
    )
    total = await db.scalar(select(func.count()).select_from(Repo).where(condition)) or 0
    repos = list(
        (
            await db.scalars(
                select(Repo)
                .where(condition)
                .order_by(Repo.updated_at.desc(), Repo.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return RepoPage(
        items=await repo_summaries(db, repos, user),
        total=total,
        page=page,
        page_size=page_size,
    )


async def _ensure_name_available(
    db: AsyncSession,
    *,
    owner_id: int,
    name: str,
    exclude_repo_id: int | None = None,
) -> None:
    stmt = select(Repo.id).where(Repo.owner_id == owner_id, Repo.name == name)
    if exclude_repo_id is not None:
        stmt = stmt.where(Repo.id != exclude_repo_id)
    if await db.scalar(stmt) is not None:
        raise AppError(409, "repo_name_taken", "仓库名称已存在")


async def create_repo(db: AsyncSession, *, user: User, payload: RepoCreate) -> RepoDetail:
    await _ensure_name_available(db, owner_id=user.id, name=payload.name)
    repo = Repo(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        visibility=payload.visibility.value,
    )
    db.add(repo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppError(409, "repo_name_taken", "仓库名称已存在") from None
    await db.refresh(repo)
    return await get_repo_detail(db, repo, user)


async def update_repo(
    db: AsyncSession,
    *,
    repo: Repo,
    user: User,
    payload: RepoUpdate,
) -> RepoDetail:
    fields = payload.model_dump(exclude_unset=True, mode="json")
    if not fields:
        raise AppError(400, "bad_request", "请求不合法")

    if "name" in fields:
        name = str(fields["name"])
        await _ensure_name_available(
            db,
            owner_id=repo.owner_id,
            name=name,
            exclude_repo_id=repo.id,
        )
        repo.name = name
    if "description" in fields:
        repo.description = str(fields["description"])
    if "visibility" in fields:
        repo.visibility = str(fields["visibility"])

    # 成功写入即刷新 updated_at（即使值没有变化）。
    repo.updated_at = utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppError(409, "repo_name_taken", "仓库名称已存在") from None

    return await get_repo_detail(db, repo, user)


async def delete_repo(db: AsyncSession, repo: Repo) -> None:
    """删除仓库：先清该仓库全部对话（messages 由外键级联），再删仓库本身。"""
    await db.execute(
        delete(Conversation).where(
            Conversation.scope_type == ChatScope.repo.value,
            Conversation.scope_id == repo.id,
        )
    )
    await db.delete(repo)
    await db.commit()


async def list_repo_papers(
    db: AsyncSession,
    *,
    repo: Repo,
    page: int,
    page_size: int,
) -> PaperPage:
    """仓库论文列表：包含已归档但仍在仓库中的论文（不套 visible_paper_conditions）。"""
    total = (
        await db.scalar(
            select(func.count()).select_from(RepoPaper).where(RepoPaper.repo_id == repo.id)
        )
        or 0
    )
    rows = (
        await db.execute(
            select(Paper, RepoPaper.added_at)
            .join(RepoPaper, RepoPaper.paper_id == Paper.id)
            .where(RepoPaper.repo_id == repo.id)
            .order_by(RepoPaper.added_at.desc(), RepoPaper.paper_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return PaperPage(
        items=[PaperBrief.model_validate(paper) for paper, _ in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


async def add_repo_papers(
    db: AsyncSession,
    *,
    repo: Repo,
    paper_ids: list[int],
) -> RepoPaperAddResult:
    """批量添加论文：去重保序，逐篇判定 not_found → duplicate → not_ready → archived。"""
    ordered: list[int] = []
    seen: set[int] = set()
    for paper_id in paper_ids:
        if paper_id in seen:
            continue
        seen.add(paper_id)
        ordered.append(paper_id)

    existing = set(
        (
            await db.scalars(
                select(RepoPaper.paper_id).where(
                    RepoPaper.repo_id == repo.id,
                    RepoPaper.paper_id.in_(ordered),
                )
            )
        ).all()
    )
    papers = {
        paper.id: paper
        for paper in (await db.scalars(select(Paper).where(Paper.id.in_(ordered)))).all()
    }

    added: list[PaperBrief] = []
    ignored: list[IgnoredPaper] = []
    now = utcnow()
    for paper_id in ordered:
        paper = papers.get(paper_id)
        if paper is None:
            ignored.append(IgnoredPaper(paper_id=paper_id, reason=RepoPaperAddReason.not_found))
            continue
        if paper_id in existing:
            ignored.append(IgnoredPaper(paper_id=paper_id, reason=RepoPaperAddReason.duplicate))
            continue
        if paper.status != PaperStatus.ready.value:
            ignored.append(IgnoredPaper(paper_id=paper_id, reason=RepoPaperAddReason.not_ready))
            continue
        if paper.is_archived:
            ignored.append(IgnoredPaper(paper_id=paper_id, reason=RepoPaperAddReason.archived))
            continue

        db.add(RepoPaper(repo_id=repo.id, paper_id=paper_id, added_at=now))
        added.append(PaperBrief.model_validate(paper))

    if added:
        repo.updated_at = now
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppError(409, "invalid_state", "仓库论文写入冲突，请重试") from None

    return RepoPaperAddResult(added=added, ignored=ignored)


async def remove_repo_paper(db: AsyncSession, *, repo: Repo, paper_id: int) -> None:
    """幂等移除：论文不在仓库中也返回成功；只有实际删除才刷新仓库 updated_at。"""
    existing = await db.scalar(
        select(RepoPaper.id).where(
            RepoPaper.repo_id == repo.id,
            RepoPaper.paper_id == paper_id,
        )
    )
    if existing is None:
        return
    await db.execute(
        delete(RepoPaper).where(RepoPaper.repo_id == repo.id, RepoPaper.paper_id == paper_id)
    )
    repo.updated_at = utcnow()
    await db.commit()


def require_starable(repo: Repo, user: User) -> None:
    """Star 判定顺序的第二、三步；第一步（可见性 404）由 get_visible_repo 负责。"""
    if not is_public_repo(repo):
        raise AppError(400, "repo_not_public", "私有仓库不能 Star")
    if repo.owner_id == user.id:
        raise AppError(400, "cannot_star_own_repo", "不能 Star 自己的仓库")


async def set_repo_star(
    db: AsyncSession,
    *,
    repo: Repo,
    user: User,
    starred: bool,
) -> StarResult:
    """幂等 star/取消 star；Star 不刷新仓库 updated_at。"""
    require_starable(repo, user)

    existing = await db.scalar(
        select(RepoStar.id).where(
            RepoStar.repo_id == repo.id,
            RepoStar.user_id == user.id,
        )
    )
    if starred and existing is None:
        db.add(RepoStar(repo_id=repo.id, user_id=user.id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
    elif not starred and existing is not None:
        await db.execute(
            delete(RepoStar).where(
                RepoStar.repo_id == repo.id,
                RepoStar.user_id == user.id,
            )
        )
        await db.commit()

    # star_count 包含已停用账号的 star（契约 §10.10）。
    star_count = (
        await db.scalar(
            select(func.count()).select_from(RepoStar).where(RepoStar.repo_id == repo.id)
        )
        or 0
    )
    return StarResult(starred=starred, star_count=star_count)
