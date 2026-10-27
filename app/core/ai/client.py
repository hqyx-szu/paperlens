from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger("paperlens.ai")

_TEMPERATURE = 0.2
_RETRY_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 0.5


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    message: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] | None = None


class LLMClient:
    """LLM 调用的可替换接口；测试使用假客户端，线上使用 DeepSeekClient。"""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        raise NotImplementedError

    @property
    def model(self) -> str:
        return ""


def _message_to_dict(message: Any) -> dict[str, Any]:
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": message.content or "",
    }
    if message.tool_calls:
        assistant_message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in message.tool_calls
        ]
    return assistant_message


class DeepSeekClient(LLMClient):
    """基于 OpenAI 兼容接口的 DeepSeek 客户端。

    客户端在实例化时才创建，未配置 API Key 时不会影响应用启动。
    """

    def __init__(self) -> None:
        from openai import AsyncOpenAI

        settings = get_settings()
        if not settings.ai_configured:
            raise AppError(503, "llm_unavailable", "AI 服务未配置")
        self._client = AsyncOpenAI(
            api_key=(settings.deepseek_api_key or "").strip(),
            base_url=settings.deepseek_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )
        self._model = settings.deepseek_model
        self._max_tokens = settings.llm_max_output_tokens

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        response = await self._create(
            model=self._model,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
            temperature=_TEMPERATURE,
            max_tokens=self._max_tokens,
        )
        choice = response.choices[0]
        message = choice.message
        tool_calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
            for call in (message.tool_calls or [])
        ]
        if response.usage:
            logger.info(
                "llm completion",
                extra={"model": self._model, "prompt_tokens": response.usage.prompt_tokens},
            )
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            message=_message_to_dict(message),
            usage=response.usage.model_dump() if response.usage else None,
        )

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        stream = await self._create(
            model=self._model,
            messages=messages,
            stream=True,
            temperature=_TEMPERATURE,
            max_tokens=self._max_tokens,
        )
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                elif getattr(chunk, "usage", None):
                    logger.info(
                        "llm stream usage",
                        extra={
                            "model": self._model,
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                        },
                    )
        except Exception as exc:
            raise _translate_error(exc) from None

    async def _create(self, **kwargs: Any) -> Any:
        from openai import APIError

        last_error: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - 统一转换为 AppError
                if not isinstance(exc, APIError):
                    raise
                last_error = exc
                if not _is_retryable(exc) or attempt == _RETRY_ATTEMPTS - 1:
                    raise _translate_error(exc) from None
                logger.warning("llm request retrying", extra={"attempt": attempt + 1})
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
        raise _translate_error(last_error)  # pragma: no cover - 循环必然返回或抛出


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _is_retryable(exc: Exception) -> bool:
    status = _status_code(exc)
    return status == 429 or (status is not None and status >= 500)


def _translate_error(exc: Exception) -> AppError:
    from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError

    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, APITimeoutError):
        return AppError(504, "llm_timeout", "AI 生成超时，请稍后重试")
    if isinstance(exc, APIConnectionError):
        return AppError(503, "llm_unavailable", "AI 服务未配置或暂不可用")
    if isinstance(exc, AuthenticationError):
        return AppError(503, "llm_unavailable", "AI 服务未配置或暂不可用")
    if isinstance(exc, APIStatusError):
        return AppError(502, "llm_failed", "AI 生成失败，请稍后重试")
    return AppError(502, "llm_failed", "AI 生成失败，请稍后重试")


_llm_client: DeepSeekClient | None = None


def get_llm_client() -> LLMClient:
    """返回进程内复用的默认客户端（懒加载，未配置时抛 503）。"""
    global _llm_client
    if _llm_client is None:
        _llm_client = DeepSeekClient()
    return _llm_client


def reset_llm_client() -> None:
    global _llm_client
    _llm_client = None
