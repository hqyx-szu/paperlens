from __future__ import annotations

import math
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.core.ai.client import LLMResponse, ToolCall

_CONCEPT_DIM = 8
_CONCEPTS = {
    "transformer": 0,
    "attention": 1,
    "注意力": 1,
    "猫": 2,
    "cat": 2,
    "宠物": 2,
    "数据库": 3,
    "database": 3,
    "sql": 3,
    "菜谱": 4,
    "cooking": 4,
}
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


class FakeEmbeddingProvider:
    """确定性"概念桶"向量：同概念不同语言映射到同一维，用来验证向量召回。

    仅用于测试同一批数据的写入与检索；不模拟真实语义。
    """

    name = "fake-embedding"

    def __init__(self, dim: int = _CONCEPT_DIM) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        vector = [0.0] * self._dim
        matched = False
        for concept, index in _CONCEPTS.items():
            if concept in lowered and index < self._dim:
                vector[index] += 1.0
                matched = True
        if not matched:
            for token in _TOKEN_RE.findall(lowered):
                vector[hash(token) % self._dim] += 0.5
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def response_with_tool_call(
    name: str,
    arguments: str = "{}",
    call_id: str = "call_1",
) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        },
    )


def final_response(content: str = "最终答案") -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=[],
        message={"role": "assistant", "content": content},
    )


class FakeLLMClient:
    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        stream_parts: list[str] | None = None,
        stream_sequences: list[list[str]] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.stream_parts = stream_parts or ["回答", "[1]"]
        self.stream_sequences = list(stream_sequences or [])
        self.stream_error = stream_error
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []
        self.stream_calls: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.calls.append((messages, tools))
        return self.responses.pop(0)

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        self.stream_calls.append(messages)
        if self.stream_error is not None:
            raise self.stream_error
        parts = self.stream_sequences.pop(0) if self.stream_sequences else self.stream_parts
        for part in parts:
            yield part
