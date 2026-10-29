"""回填片段向量。

用法（需要 EMBEDDING_PROVIDER=local 并已下载模型）：

    uv run python -m scripts.embed_chunks --limit 0

未启用向量或模型不可用时直接返回 0，不会改动数据。
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.ai.embeddings import get_embedding_provider
from app.core.config import get_settings
from app.db import get_engine
from app.services.embeddings import embed_missing_chunks


async def _run(limit: int) -> int:
    settings = get_settings()
    provider = get_embedding_provider()
    if provider is None:
        print("EMBEDDING_PROVIDER=none，跳过向量回填。")
        return 0
    print(f"model={settings.embedding_model} provider={settings.embedding_provider}")
    written = await embed_missing_chunks(limit=limit)
    print(f"embedded_chunks={written}")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="为缺少向量的论文片段补生成 embedding")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个片段，0 表示全部")
    args = parser.parse_args()
    asyncio.run(_run(args.limit))
    asyncio.run(get_engine().dispose())


if __name__ == "__main__":
    main()
