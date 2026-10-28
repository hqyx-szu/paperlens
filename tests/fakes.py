from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.core.ai.client import LLMResponse, ToolCall


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
