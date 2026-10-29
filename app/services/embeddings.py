"""向量索引与向量检索。

- 写入：`embed_paper_chunks()` 在论文解析完成后批量生成片段向量（失败不阻塞论文就绪）。
- 检索：`vector_hits()` 返回 `(chunk_id, 余弦相似度)`，调用方与关键词结果做 RRF 融合。
- 未配置/未下载模型时所有函数直接返回空，检索退化为纯关键词，行为与 D3 一致。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.embeddings import EmbeddingProvider, get_embedding_provider
from app.core.config import get_settings
from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import ChunkEmbedding, Paper, PaperChunk, RepoPaper
from app.services.paper_queries import visible_paper_conditions

logger = logging.getLogger("paperlens.embeddings")

SearchMode = Literal["global", "repo", "paper"]
_INSERT_BATCH = 500


def pack_vector(values: Sequence[float]) -> bytes:
    return np.asarray(values, dtype=np.float32).tobytes()


def unpack_vector(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim)


async def _upsert_embeddings(
    db: AsyncSession,
    provider: EmbeddingProvider,
    rows: list[tuple[int, str]],
) -> int:
    written = 0
    batch_size = provider_batch_size()
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vectors = await provider.embed_documents([content for _, content in batch])
        values = [
            {
                "chunk_id": chunk_id,
                "model": provider.name,
                "dim": len(vector),
                "vector": pack_vector(vector),
            }
            for (chunk_id, _), vector in zip(batch, vectors, strict=True)
        ]
        for offset in range(0, len(values), _INSERT_BATCH):
            statement = mysql_insert(ChunkEmbedding).values(values[offset : offset + _INSERT_BATCH])
            statement = statement.on_duplicate_key_update(
                model=statement.inserted.model,
                dim=statement.inserted.dim,
                vector=statement.inserted.vector,
            )
            await db.execute(statement)
        written += len(values)
    return written


def provider_batch_size() -> int:
    return get_settings().embedding_batch_size


async def embed_paper_chunks(paper_id: int) -> int:
    """为单篇论文的全部片段生成向量；任何失败只记录日志，不影响论文状态。"""
    provider = get_embedding_provider()
    if provider is None:
        return 0

    async with get_session_factory()() as db:
        rows = (
            await db.execute(
                select(PaperChunk.id, PaperChunk.content).where(PaperChunk.paper_id == paper_id)
            )
        ).all()
        if not rows:
            return 0
        try:
            written = await _upsert_embeddings(
                db, provider, [(int(cid), content) for cid, content in rows]
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("failed to embed paper chunks", extra={"paper_id": paper_id})
            return 0
    logger.info("embedded paper chunks", extra={"paper_id": paper_id, "chunks": written})
    return written


async def embed_missing_chunks(*, limit: int = 0) -> int:
    """回填脚本入口：为还没有向量的片段补生成（论文已删除的残留向量会被忽略）。"""
    provider = get_embedding_provider()
    if provider is None:
        return 0

    async with get_session_factory()() as db:
        statement = (
            select(PaperChunk.id, PaperChunk.content)
            .outerjoin(ChunkEmbedding, ChunkEmbedding.chunk_id == PaperChunk.id)
            .where(ChunkEmbedding.chunk_id.is_(None), PaperChunk.char_count > 0)
            .order_by(PaperChunk.id)
        )
        if limit:
            statement = statement.limit(limit)
        rows = (await db.execute(statement)).all()
        if not rows:
            return 0
        try:
            written = await _upsert_embeddings(
                db, provider, [(int(cid), content) for cid, content in rows]
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("failed to backfill chunk embeddings")
            return 0
    return written


async def embed_query_text(text: str) -> np.ndarray | None:
    provider = get_embedding_provider()
    keyword = text.strip()
    if provider is None or not keyword:
        return None
    try:
        vector = await provider.embed_query(keyword)
    except Exception:
        logger.exception("failed to embed query")
        return None
    return np.asarray(vector, dtype=np.float32)


async def vector_hits(
    db: AsyncSession,
    *,
    query_vector: np.ndarray,
    limit: int,
    mode: SearchMode,
    repo_id: int | None = None,
    paper_ids: Sequence[int] | None = None,
) -> list[tuple[int, float]]:
    """按余弦相似度返回 top-N `(chunk_id, score)`。

    - global：只看 ready 且未归档论文（与全局目录一致）
    - repo：只看该仓库的论文（含已归档、不含已移除）
    - paper：只看指定论文（归档仍可对话）
    """
    statement = (
        select(ChunkEmbedding.chunk_id, ChunkEmbedding.vector, ChunkEmbedding.dim)
        .join(PaperChunk, PaperChunk.id == ChunkEmbedding.chunk_id)
        .join(Paper, Paper.id == PaperChunk.paper_id)
    )
    if mode == "repo":
        if repo_id is None:
            raise ValueError("repo mode requires repo_id")
        statement = statement.join(RepoPaper, RepoPaper.paper_id == PaperChunk.paper_id).where(
            RepoPaper.repo_id == repo_id
        )
    elif mode == "paper":
        if not paper_ids:
            raise ValueError("paper mode requires paper_ids")
        statement = statement.where(PaperChunk.paper_id.in_(list(paper_ids)))
    else:
        statement = statement.where(*visible_paper_conditions())

    rows = (await db.execute(statement)).all()
    if not rows:
        return []

    candidates = [(int(row[0]), row[1], int(row[2])) for row in rows]
    dims = {dim for _, _, dim in candidates}
    if len(dims) != 1 or dims.pop() != int(query_vector.shape[0]):
        logger.warning("embedding dim mismatch, skip vector search")
        return []
    dim = int(query_vector.shape[0])

    matrix = np.vstack([unpack_vector(blob, dim) for _, blob, _ in candidates])
    scores = matrix @ query_vector
    take = min(limit, int(scores.shape[0]))
    if take <= 0:
        return []
    top_indexes = np.argpartition(-scores, take - 1)[:take].tolist()
    ordered = sorted((int(index) for index in top_indexes), key=lambda index: -float(scores[index]))
    # 相似度 <= 0 的片段视为不相关，避免零分结果污染证据与 RRF 融合。
    return [
        (candidates[index][0], float(scores[index]))
        for index in ordered
        if float(scores[index]) > 0
    ]


async def load_chunk_pairs(
    db: AsyncSession,
    chunk_ids: Sequence[int],
    *,
    include_archived: bool = False,
    require_ready: bool = True,
) -> dict[int, tuple[PaperChunk, Paper]]:
    """按 chunk_id 批量取 (片段, 论文)，供融合后组装证据。"""
    if not chunk_ids:
        return {}
    statement = (
        select(PaperChunk, Paper)
        .join(Paper, Paper.id == PaperChunk.paper_id)
        .where(PaperChunk.id.in_(list(chunk_ids)))
    )
    if require_ready:
        statement = statement.where(Paper.status == PaperStatus.ready.value)
    if not include_archived:
        statement = statement.where(Paper.is_archived.is_(False))
    rows = (await db.execute(statement)).all()
    return {chunk.id: (chunk, paper) for chunk, paper in rows}
