"""AI 层冒烟脚本：直接调用 D1 的 LLM 客户端，不经过 HTTP。

用法：
    python -m scripts.ai_smoke
    python -m scripts.ai_smoke --message "用一句话介绍 PaperLens" --stream

需要先在 .env 中配置 DEEPSEEK_API_KEY；未配置时按设计返回 503 llm_unavailable。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.ai.client import get_llm_client, reset_llm_client
from app.core.config import get_settings
from app.core.exceptions import AppError

SYSTEM_PROMPT = "你是 PaperLens 的助手，请用中文简洁回答。"


async def run(message: str, stream: bool) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    if not settings.ai_configured:
        print("未配置 DEEPSEEK_API_KEY：AI 客户端会按设计返回 503 llm_unavailable。")
        print("在 .env 中填写 DEEPSEEK_API_KEY 后重新运行即可真实调用模型。")
        return 1

    client = get_llm_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]
    try:
        print(f"model: {client.model}")
        print("answer: ", end="", flush=True)
        if stream:
            async for chunk in client.stream(messages):
                print(chunk, end="", flush=True)
            print()
        else:
            response = await client.complete(messages)
            print(response.content or "(空响应)")
            if response.usage:
                print(f"usage: {response.usage}")
    except AppError as exc:
        print(f"\n[{exc.status_code}] {exc.code}: {exc.message}")
        return 1
    finally:
        reset_llm_client()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 层冒烟测试")
    parser.add_argument("--message", default="用一句话介绍 PaperLens。")
    parser.add_argument("--stream", action="store_true", help="使用流式输出")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.message, args.stream)))


if __name__ == "__main__":
    main()
