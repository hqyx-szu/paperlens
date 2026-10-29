from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.models.base import Base


class PaperSummary(Base):
    """论文四维总结缓存：键为 paper_id + level + content_hash + prompt_version + model。"""

    __tablename__ = "paper_summaries"
    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "level",
            "content_hash",
            "prompt_version",
            "model",
            name="uq_paper_summaries_cache_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
