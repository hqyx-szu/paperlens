"""向量检索冒烟：验证本地 embedding 模型能按语义排序（不写库）。

用法：

    uv run python -m scripts.rag_smoke
    uv run python -m scripts.rag_smoke --query "有没有不用循环网络的翻译模型？"
"""

from __future__ import annotations

import argparse
import asyncio

import numpy as np

from app.core.ai.embeddings import get_embedding_provider
from app.core.config import get_settings

SAMPLES = [
    "本文提出一种基于自注意力机制的神经机器翻译模型，摒弃了循环神经网络结构。",
    "本实验采用高效液相色谱法测定土壤中多环芳烃的含量。",
    "The proposed Transformer architecture replaces recurrence with self-attention.",
    "我们统计了城市轨道交通的客流量变化趋势与影响因素。",
    "本文比较了 BERT 与 GPT 两类预训练语言模型在下游任务上的表现。",
]
QUERIES = [
    "有没有不用循环网络的机器翻译模型？",
    "土壤污染物的检测方法",
    "地铁客流",
]


async def _run(queries: list[str]) -> None:
    settings = get_settings()
    provider = get_embedding_provider()
    if provider is None:
        print("EMBEDDING_PROVIDER=none：未启用向量检索。")
        return
    print(f"provider={settings.embedding_provider} model={settings.embedding_model}")

    documents = np.asarray(await provider.embed_documents(SAMPLES), dtype=np.float32)
    for query in queries:
        vector = np.asarray(await provider.embed_query(query), dtype=np.float32)
        scores = documents @ vector
        ranking = np.argsort(-scores)
        print(f"\nquery: {query}")
        for index in ranking:
            print(f"  {float(scores[index]):.3f}  {SAMPLES[int(index)]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="向量检索冒烟（语义排序）")
    parser.add_argument("--query", action="append", help="自定义查询，可多次传入")
    args = parser.parse_args()
    asyncio.run(_run(args.query or QUERIES))


if __name__ == "__main__":
    main()
