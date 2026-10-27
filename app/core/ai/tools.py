from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import AppError

from .client import LLMClient


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]


class ToolRegistry:
    """服务端注册的工具集合；工具范围由闭包绑定，模型无法越界。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._tools.values()
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise AppError(400, "unknown_tool", "未知工具")
        return await spec.handler(arguments)


async def run_tool_loop(
    client: LLMClient,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
    max_steps: int = 4,
) -> list[dict[str, Any]]:
    """只处理工具阶段（非流式）；拿到最终回答后由调用方流式输出。"""
    for _ in range(max_steps):
        response = await client.complete(messages, tools=registry.specs())
        if not response.tool_calls:
            messages.append({"role": "assistant", "content": response.content or ""})
            return messages

        messages.append(response.message)
        for call in response.tool_calls:
            try:
                arguments = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            result = await registry.execute(call.name, arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    raise AppError(504, "tool_loop_exceeded", "AI 查询步骤过多")
