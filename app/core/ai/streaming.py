from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

HEARTBEAT = ": ping\n\n"
HEARTBEAT_INTERVAL_SECONDS = 15.0


def sse(event: str, data: dict[str, Any]) -> str:
    """格式化一个 SSE 事件（event + data + 空行）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def with_heartbeat(
    events: AsyncIterator[str],
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    """在事件流空闲时每 interval 秒发送一次 SSE 注释心跳。"""
    iterator = events.__aiter__()
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield HEARTBEAT
                continue
            task, pending = pending, None
            try:
                yield task.result()
            except StopAsyncIteration:
                return
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
