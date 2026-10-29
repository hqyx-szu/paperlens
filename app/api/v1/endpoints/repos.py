from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.ai import get_llm_client, sse_response, with_heartbeat
from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.core.exceptions import AppError
from app.core.repos import RepoScope
from app.db import get_db
from app.models import Message, User
from app.schemas.chat import ChatRequest, MessagePage
from app.schemas.paper import PaperPage
from app.schemas.repo import (
    ConversationPage,
    RelatedRepoPage,
    RepoCreate,
    RepoDetail,
    RepoPage,
    RepoPaperAddRequest,
    RepoPaperAddResult,
    RepoUpdate,
    StarResult,
)
from app.services.chat import get_conversation, get_or_create_conversation
from app.services.repo_chat import repo_chat_events, repo_chunk_count
from app.services.repo_conversations import conversation_messages, list_conversations
from app.services.repo_related import related_repos
from app.services.repos import (
    add_repo_papers,
    create_repo,
    delete_repo,
    get_repo_detail,
    get_visible_repo,
    list_repo_papers,
    list_repos,
    remove_repo_paper,
    require_repo_manager,
    set_repo_star,
    update_repo,
)
from app.services.session_service import get_owned_session, list_messages

router = APIRouter(
    prefix="/repos",
    tags=["repos"],
    dependencies=[Depends(get_current_user)],
)


@router.post("", response_model=RepoDetail, status_code=201)
async def create_repo_endpoint(
    payload: RepoCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> RepoDetail:
    return await create_repo(db, user=user, payload=payload)


@router.get("", response_model=RepoPage)
async def list_repos_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    scope: Annotated[RepoScope, Query()] = RepoScope.mine,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RepoPage:
    return await list_repos(
        db,
        user=user,
        scope=scope,
        page=page,
        page_size=page_size,
    )


@router.get("/related", response_model=RelatedRepoPage)
async def related_repos_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    session_id: Annotated[int, Query(ge=1)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RelatedRepoPage:
    # 契约 §3 路由注意：/related 必须注册在 /{repo_id} 之前，否则会被当作 repo_id。
    search_session = await get_owned_session(db, session_id=session_id, user_id=user.id)
    return await related_repos(
        db,
        session=search_session,
        user_id=user.id,
        page=page,
        page_size=page_size,
    )


@router.get("/{repo_id}", response_model=RepoDetail)
async def get_repo_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> RepoDetail:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    return await get_repo_detail(db, repo, user)


@router.patch("/{repo_id}", response_model=RepoDetail)
async def update_repo_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    payload: RepoUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> RepoDetail:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    require_repo_manager(repo, user)
    return await update_repo(db, repo=repo, user=user, payload=payload)


@router.delete("/{repo_id}", status_code=204)
async def delete_repo_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    require_repo_manager(repo, user)
    await delete_repo(db, repo)


@router.get("/{repo_id}/papers", response_model=PaperPage)
async def list_repo_papers_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaperPage:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    return await list_repo_papers(db, repo=repo, page=page, page_size=page_size)


@router.post("/{repo_id}/papers", response_model=RepoPaperAddResult)
async def add_repo_papers_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    payload: RepoPaperAddRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> RepoPaperAddResult:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    require_repo_manager(repo, user)
    return await add_repo_papers(db, repo=repo, paper_ids=payload.paper_ids)


@router.delete("/{repo_id}/papers/{paper_id}", status_code=204)
async def remove_repo_paper_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    paper_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    require_repo_manager(repo, user)
    await remove_repo_paper(db, repo=repo, paper_id=paper_id)


@router.post("/{repo_id}/star", response_model=StarResult)
async def star_repo_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> StarResult:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    return await set_repo_star(db, repo=repo, user=user, starred=True)


@router.delete("/{repo_id}/star", response_model=StarResult)
async def unstar_repo_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> StarResult:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    return await set_repo_star(db, repo=repo, user=user, starred=False)


@router.post("/{repo_id}/chat/stream")
async def repo_chat_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    payload: ChatRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    if await repo_chunk_count(db, repo.id) == 0:
        raise AppError(409, "repo_empty", "仓库还没有可用的论文，不能对话")

    client = get_llm_client()
    conversation = await get_or_create_conversation(
        db,
        user_id=user.id,
        scope=ChatScope.repo,
        scope_id=repo.id,
    )
    history = list(
        (
            await db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.role != MessageRole.system.value,
                )
                .order_by(Message.id.desc())
                .limit(12)
            )
        ).all()
    )[::-1]

    user_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.user.value,
        content=payload.message,
        status=MessageStatus.complete.value,
        citations=[],
    )
    db.add(user_message)
    await db.commit()
    await db.refresh(user_message)

    events = repo_chat_events(
        client=client,
        repo_id=repo.id,
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        history=history,
        question=payload.message,
    )
    return sse_response(with_heartbeat(events))


@router.get("/{repo_id}/messages", response_model=MessagePage)
async def repo_messages_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> MessagePage:
    # 只返回当前用户自己的对话，即使他是私有仓库的创建者/管理员。
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    conversation = await get_conversation(
        db,
        user_id=user.id,
        scope=ChatScope.repo,
        scope_id=repo.id,
    )
    if conversation is None:
        return MessagePage(items=[], has_more=False, next_before_id=None)
    return await list_messages(
        db,
        conversation_id=conversation.id,
        limit=limit,
        before_id=before_id,
    )


@router.get("/{repo_id}/conversations", response_model=ConversationPage)
async def repo_conversations_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConversationPage:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    return await list_conversations(
        db,
        repo=repo,
        user=user,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{repo_id}/conversations/{conversation_id}/messages",
    response_model=MessagePage,
)
async def repo_conversation_messages_endpoint(
    repo_id: Annotated[int, Path(ge=1)],
    conversation_id: Annotated[int, Path(ge=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> MessagePage:
    repo = await get_visible_repo(db, repo_id=repo_id, user=user)
    return await conversation_messages(
        db,
        repo=repo,
        user=user,
        conversation_id=conversation_id,
        limit=limit,
        before_id=before_id,
    )
