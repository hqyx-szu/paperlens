from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.repos import RepoVisibility
from app.core.time import utcnow
from app.models.base import Base


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_repos_owner_name"),
        Index("ix_repos_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
        server_default="",
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=RepoVisibility.private.value,
        server_default=RepoVisibility.private.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class RepoPaper(Base):
    __tablename__ = "repo_papers"
    __table_args__ = (UniqueConstraint("repo_id", "paper_id", name="uq_repo_papers_repo_paper"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    paper_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class RepoStar(Base):
    __tablename__ = "repo_stars"
    __table_args__ = (UniqueConstraint("repo_id", "user_id", name="uq_repo_stars_repo_user"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
