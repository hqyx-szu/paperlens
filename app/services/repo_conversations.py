from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chat import ChatScope
from app.core.exceptions import AppError
from app.core.roles import UserRole
from app.models import Conversation, Message, Repo, User
from app.schemas.chat import MessagePage
from app.schemas.common import UserBrief
from app.schemas.repo import ConversationItem, ConversationPage
from app.services.repos import is_public_repo
from app.services.session_service import list_messages


def can_view_all_conversations(repo: Repo, user: User) -> bool:
    """私有仓库：创建者与管理员可看全部对话；公开仓库只有发起者本人。"""
    if is_public_repo(repo):
        return False
    return repo.owner_id == user.id or user.role == UserRole.admin.value


async def list_conversations(
    db: AsyncSession,
    *,
    repo: Repo,
    user: User,
    page: int,
    page_size: int,
) -> ConversationPage:
    scope = (
        Conversation.scope_type == ChatScope.repo.value,
        Conversation.scope_id == repo.id,
    )
    filters = list(scope)
    if not can_view_all_conversations(repo, user):
        filters.append(Conversation.user_id == user.id)

    aggregates = (
        select(
            Message.conversation_id.label("conversation_id"),
            func.count().label("message_count"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    total = await db.scalar(select(func.count()).select_from(Conversation).where(*filters)) or 0
    rows = (
        await db.execute(
            select(
                Conversation,
                func.coalesce(aggregates.c.message_count, 0),
                aggregates.c.last_message_at,
            )
            .outerjoin(aggregates, aggregates.c.conversation_id == Conversation.id)
            .where(*filters)
            .order_by(
                aggregates.c.last_message_at.is_(None),
                aggregates.c.last_message_at.desc(),
                Conversation.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    user_ids = {conversation.user_id for conversation, _, _ in rows}
    briefs = (
        dict(
            (
                await db.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))
            ).all()
        )
        if user_ids
        else {}
    )

    items = [
        ConversationItem(
            id=conversation.id,
            user=UserBrief(
                id=conversation.user_id,
                display_name=briefs.get(conversation.user_id),
            ),
            message_count=int(count or 0),
            last_message_at=last_message_at,
            created_at=conversation.created_at,
        )
        for conversation, count, last_message_at in rows
    ]
    return ConversationPage(items=items, total=total, page=page, page_size=page_size)


async def _repo_conversation(
    db: AsyncSession,
    *,
    repo: Repo,
    conversation_id: int,
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.scope_type == ChatScope.repo.value,
            Conversation.scope_id == repo.id,
        )
    )
    if conversation is None:
        raise AppError(404, "conversation_not_found", "对话不存在")
    return conversation


def _require_conversation_readable(repo: Repo, user: User, conversation: Conversation) -> None:
    if conversation.user_id == user.id:
        return
    if not is_public_repo(repo):
        # 私有仓库能走到这里说明是创建者或管理员，可读全部对话。
        return
    if user.role == UserRole.admin.value and repo.owner_id != user.id:
        # 公开仓库中管理员（非创建者、非发起者）显式 403；
        # 创建者与其他非发起者一律 404，避免泄露对话是否存在。
        raise AppError(403, "forbidden", "无权访问")
    raise AppError(404, "conversation_not_found", "对话不存在")


async def conversation_messages(
    db: AsyncSession,
    *,
    repo: Repo,
    user: User,
    conversation_id: int,
    limit: int,
    before_id: int | None,
) -> MessagePage:
    conversation = await _repo_conversation(
        db,
        repo=repo,
        conversation_id=conversation_id,
    )
    _require_conversation_readable(repo, user, conversation)
    return await list_messages(
        db,
        conversation_id=conversation.id,
        limit=limit,
        before_id=before_id,
    )
