from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.chat import ChatScope, MessageRole, MessageStatus
from app.core.time import utcnow
from app.models.base import Base


class SearchSession(Base):
    __tablename__ = "search_sessions"
    __table_args__ = (Index("ix_search_sessions_user_created", "user_id", "created_at", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    query: Mapped[str] = mapped_column(String(2000), nullable=False)
    query_type: Mapped[str] = mapped_column(String(16), nullable=False)
    paper_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    system_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    papers: Mapped[list[SessionPaper]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionPaper.rank",
    )
    evidence: Mapped[list[SessionEvidence]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionEvidence.rank",
    )


class SessionPaper(Base):
    __tablename__ = "session_papers"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "paper_id",
            name="uq_session_papers_session_paper",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("search_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    paper_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    session: Mapped[SearchSession] = relationship(back_populates="papers")


class SessionEvidence(Base):
    __tablename__ = "session_evidence"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "chunk_id",
            name="uq_session_evidence_session_chunk",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("search_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    paper_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("paper_chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)

    session: Mapped[SearchSession] = relationship(back_populates="evidence")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "scope_type",
            "scope_id",
            name="uq_conversations_user_scope",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ChatScope.session.value,
    )
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_id_id", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=MessageRole.user.value,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=MessageStatus.complete.value,
        server_default=MessageStatus.complete.value,
    )
    citations: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
