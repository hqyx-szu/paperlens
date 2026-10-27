from __future__ import annotations

import asyncio
import json

import pytest

from app.core.ai import (
    Citation,
    DeepSeekClient,
    ToolRegistry,
    ToolSpec,
    citations_are_valid,
    run_tool_loop,
    sse,
    sse_response,
    with_heartbeat,
)
from app.core.config import get_settings
from app.core.exceptions import AppError
from tests.fakes import FakeLLMClient, final_response, response_with_tool_call


def _registry(record: list[dict]) -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(arguments: dict) -> dict:
        record.append(arguments)
        return {"ok": True, "echo": arguments}

    registry.register(
        ToolSpec(
            name="lookup",
            description="查找论文",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=handler,
        )
    )
    return registry


async def test_tool_loop_executes_tool_then_returns_answer() -> None:
    record: list[dict] = []
    client = FakeLLMClient(
        [response_with_tool_call("lookup", '{"query": "RAG"}'), final_response("答案 [1]")]
    )
    messages = [{"role": "user", "content": "什么是 RAG"}]

    result = await run_tool_loop(client, messages, _registry(record), max_steps=4)

    assert record == [{"query": "RAG"}]
    assert result[-1] == {"role": "assistant", "content": "答案 [1]"}
    tool_message = next(message for message in result if message["role"] == "tool")
    assert tool_message["tool_call_id"] == "call_1"
    assert json.loads(tool_message["content"])["ok"] is True
    assert client.calls[0][1][0]["function"]["name"] == "lookup"
    assert "query" in client.calls[0][1][0]["function"]["parameters"]["properties"]


async def test_tool_loop_exceeds_max_steps() -> None:
    registry = _registry([])
    client = FakeLLMClient([response_with_tool_call("lookup")] * 2)

    with pytest.raises(AppError) as caught:
        await run_tool_loop(client, [], registry, max_steps=2)

    assert caught.value.status_code == 504
    assert caught.value.code == "tool_loop_exceeded"


async def test_unknown_tool_raises() -> None:
    client = FakeLLMClient([response_with_tool_call("missing_tool")])

    with pytest.raises(AppError) as caught:
        await run_tool_loop(client, [], ToolRegistry(), max_steps=1)

    assert caught.value.status_code == 400
    assert caught.value.code == "unknown_tool"


async def test_invalid_tool_arguments_do_not_crash() -> None:
    record: list[dict] = []
    client = FakeLLMClient([response_with_tool_call("lookup", "not-json"), final_response("ok")])

    await run_tool_loop(client, [], _registry(record), max_steps=2)

    assert record == [{}]


def test_sse_event_format() -> None:
    event = sse("token", {"text": "这些论文"})

    assert event == 'event: token\ndata: {"text": "这些论文"}\n\n'


def test_sse_response_headers() -> None:
    async def generator():
        yield sse("done", {"status": "complete"})

    response = sse_response(generator())

    assert response.media_type == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


async def test_heartbeat_is_sent_while_idle() -> None:
    async def slow_generator():
        await asyncio.sleep(0.05)
        yield "event: done\ndata: {}\n\n"

    parts = [part async for part in with_heartbeat(slow_generator(), interval=0.01)]

    assert parts[0] == ": ping\n\n"
    assert parts[-1].startswith("event: done")


async def test_missing_api_key_raises_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.ai.client import reset_llm_client

    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    reset_llm_client()
    try:
        with pytest.raises(AppError) as caught:
            DeepSeekClient()
    finally:
        reset_llm_client()
        get_settings.cache_clear()

    assert caught.value.status_code == 503
    assert caught.value.code == "llm_unavailable"


def _citations(count: int) -> list[Citation]:
    return [
        Citation(index=index, paper_id=1, chunk_id=index, page=index, quote="原文")
        for index in range(1, count + 1)
    ]


def test_citation_validation() -> None:
    citations = _citations(2)

    assert citations_are_valid("结论 [1]，补充 [2]", citations) is True
    assert citations_are_valid("结论 [2]", citations) is True
    assert citations_are_valid("结论 [3]", citations) is False
    assert citations_are_valid("没有引用", citations) is False
    assert citations_are_valid("结论 [1]", []) is False
    assert citations_are_valid("结论 [2]", _citations(1)) is False


def test_refusal_text_is_stable() -> None:
    from app.core.ai import REFUSAL_TEXT, SYSTEM_PROMPT

    assert REFUSAL_TEXT == "根据当前资料，没有足够证据回答这个问题。"
    assert REFUSAL_TEXT in SYSTEM_PROMPT
