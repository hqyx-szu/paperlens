"""本地向量模型（sentence-transformers / BGE）封装。

设计要点：
- 默认 provider=`none`：测试与未下载模型的机器完全走关键词检索，不联网、不加载模型。
- 模型只在首次使用时加载（lazy），加载与推理都放线程池，避免阻塞事件循环。
- 向量统一 L2 归一化，检索时点积即余弦相似度。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence
from typing import Any, Protocol

from app.core.config import get_settings

logger = logging.getLogger("paperlens.embeddings")

_PROVIDER_LOCK = threading.Lock()
_provider: EmbeddingProvider | None = None
_provider_model: str | None = None


class EmbeddingProvider(Protocol):
    name: str

    @property
    def dim(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class LocalEmbeddingProvider:
    """sentence-transformers 本地模型；CPU 推理。"""

    def __init__(
        self,
        *,
        model_name: str,
        model_path: str | None,
        query_prefix: str,
        batch_size: int,
    ) -> None:
        self.name = model_name
        self.query_prefix = query_prefix
        self.batch_size = batch_size
        self._model_name = model_name
        self._model_path = model_path
        self._model: Any = None
        self._load_lock = threading.Lock()
        self._dim: int | None = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                source = self._model_path or self._model_name
                logger.info("loading embedding model", extra={"model": source})
                self._model = SentenceTransformer(source, device="cpu")
                dimension = getattr(self._model, "get_embedding_dimension", None)
                if callable(dimension):
                    self._dim = int(dimension())
                else:  # sentence-transformers < 5
                    self._dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._load()
        return int(self._dim or 0)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, list(texts))

    async def embed_query(self, text: str) -> list[float]:
        """BGE 中文模型建议给短查询加指令前缀，提升检索召回。"""
        query = f"{self.query_prefix}{text}" if self.query_prefix else text
        vectors = await asyncio.to_thread(self._encode, [query])
        return vectors[0]


def build_provider() -> EmbeddingProvider | None:
    settings = get_settings()
    if not settings.embeddings_enabled:
        return None
    return LocalEmbeddingProvider(
        model_name=settings.embedding_model,
        model_path=str(settings.embedding_model_path) if settings.embedding_model_path else None,
        query_prefix=settings.embedding_query_prefix,
        batch_size=settings.embedding_batch_size,
    )


def get_embedding_provider() -> EmbeddingProvider | None:
    """进程内单例；模型配置变化时重建（测试用 reset 或改 env + cache_clear）。"""
    global _provider, _provider_model
    settings = get_settings()
    if not settings.embeddings_enabled:
        _provider = None
        _provider_model = None
        return None
    key = (
        f"{settings.embedding_provider}|{settings.embedding_model}|{settings.embedding_model_path}"
    )
    with _PROVIDER_LOCK:
        if _provider is None or _provider_model != key:
            _provider = build_provider()
            _provider_model = key
        return _provider


def reset_embedding_provider() -> None:
    global _provider, _provider_model
    with _PROVIDER_LOCK:
        _provider = None
        _provider_model = None
