from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.exceptions import AppError
from app.core.papers import PROCESSING_STATUSES, PaperStatus
from app.core.roles import UserRole
from app.core.security import hash_password
from app.core.time import utcnow
from app.db import get_db
from app.models import Paper, Repo, User, UserSession
from app.schemas import (
    AdminStats,
    AdminUserCreate,
    AdminUserPage,
    AdminUserUpdate,
    PasswordReset,
    UserRead,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

_AVAILABLE_ADMIN = (User.role == UserRole.admin.value, User.is_active.is_(True))


def _display_name_from_email(email: str) -> str:
    return email.split("@", 1)[0][:64]


def _escape_like(value: str) -> str:
    """Treat % and _ as literals in a LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_available_admin(user: User) -> bool:
    return user.role == UserRole.admin.value and user.is_active


async def _count_available_admins(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User).where(*_AVAILABLE_ADMIN)) or 0


async def _get_user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise AppError(404, "user_not_found", "用户不存在")
    return user


@router.get("/users", response_model=AdminUserPage)
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(max_length=200)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminUserPage:
    stmt = select(User)
    keyword = q.strip()
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        stmt = stmt.where(
            or_(
                User.email.like(pattern, escape="\\"),
                User.display_name.like(pattern, escape="\\"),
            )
        )

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = await db.scalars(
        stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return AdminUserPage(
        items=[UserRead.model_validate(user) for user in rows.all()],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/users", response_model=UserRead, status_code=201)
async def create_user(
    payload: AdminUserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserRead:
    email = str(payload.email).lower()
    existing = await db.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise AppError(409, "email_taken", "该邮箱已注册")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or _display_name_from_email(email),
        role=payload.role.value,
        is_active=True,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppError(409, "email_taken", "该邮箱已注册") from None
    await db.refresh(user)
    return UserRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    payload: AdminUserUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    user_id: Annotated[int, Path(ge=1)],
) -> UserRead:
    if not payload.model_fields_set:
        raise AppError(400, "bad_request", "请求不合法")

    user = await _get_user_or_404(db, user_id)
    changes = payload.model_fields_set

    demoting = "role" in changes and payload.role == UserRole.reader
    disabling = "is_active" in changes and payload.is_active is False

    if user.id == admin.id and user.role == UserRole.admin.value and demoting:
        raise AppError(400, "cannot_demote_self", "不能修改自己的管理员角色")
    if user.id == admin.id and disabling:
        raise AppError(400, "cannot_disable_self", "不能停用自己")

    new_role = payload.role.value if "role" in changes and payload.role is not None else user.role
    new_active = payload.is_active if "is_active" in changes else user.is_active
    was_available = _is_available_admin(user)
    will_be_available = new_role == UserRole.admin.value and bool(new_active)
    if was_available and not will_be_available and await _count_available_admins(db) <= 1:
        raise AppError(400, "last_admin", "至少保留一个可用管理员")

    if "display_name" in changes:
        user.display_name = payload.display_name
    if "role" in changes and payload.role is not None:
        user.role = payload.role.value
    if "is_active" in changes and payload.is_active is not None:
        user.is_active = payload.is_active
        if not payload.is_active:
            await db.execute(delete(UserSession).where(UserSession.user_id == user.id))

    await db.commit()
    await db.refresh(user)
    return UserRead.model_validate(user)


@router.post("/users/{user_id}/reset-password", status_code=204)
async def reset_password(
    payload: PasswordReset,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int, Path(ge=1)],
) -> None:
    user = await _get_user_or_404(db, user_id)
    user.password_hash = hash_password(payload.new_password)
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.commit()


@router.get("/stats", response_model=AdminStats)
async def admin_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminStats:
    users_total = await db.scalar(select(func.count()).select_from(User)) or 0
    users_active = (
        await db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    )

    papers_total = await db.scalar(select(func.count()).select_from(Paper)) or 0
    papers_ready = (
        await db.scalar(
            select(func.count()).select_from(Paper).where(Paper.status == PaperStatus.ready.value)
        )
    ) or 0
    papers_processing = (
        await db.scalar(
            select(func.count())
            .select_from(Paper)
            .where(
                Paper.status.in_(PROCESSING_STATUSES),
                Paper.is_archived.is_(False),
            )
        )
    ) or 0
    papers_failed = (
        await db.scalar(
            select(func.count())
            .select_from(Paper)
            .where(
                Paper.status == PaperStatus.failed.value,
                Paper.is_archived.is_(False),
            )
        )
    ) or 0
    papers_archived = (
        await db.scalar(
            select(func.count())
            .select_from(Paper)
            .where(
                Paper.status == PaperStatus.ready.value,
                Paper.is_archived.is_(True),
            )
        )
    ) or 0

    repos_total = await db.scalar(select(func.count()).select_from(Repo)) or 0
    repos_private = (
        await db.scalar(select(func.count()).select_from(Repo).where(Repo.visibility == "private"))
    ) or 0

    return AdminStats(
        users_total=users_total,
        users_active=users_active,
        papers_total=papers_total,
        papers_ready=papers_ready,
        papers_processing=papers_processing,
        papers_failed=papers_failed,
        papers_archived=papers_archived,
        repos_total=repos_total,
        repos_private=repos_private,
        generated_at=utcnow(),
    )
