from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from app.core.exceptions import AppError
from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import Paper, PaperChunk
from tests.fakes import FakeLLMClient, final_response

PAPERS = "/api/v1/papers"


async def _paper(
    title: str = "Paper",
    *,
    contents: tuple[str, ...] = ("transformer 方法的原文片段",),
    status: str = PaperStatus.ready.value,
    archived: bool = False,
) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(title=title, authors=["A"], status=status, is_archived=archived)
        db.add(paper)
        await db.flush()
        for index, content in enumerate(contents):
            db.add(
                PaperChunk(
                    paper_id=paper.id,
                    chunk_index=index,
                    page=index + 1,
                    content=content,
                    char_count=len(content),
                )
            )
        await db.commit()
        await db.refresh(paper)
        return paper


def _payload(**overrides: str) -> dict[str, str]:
    base = {
        "problem": "要解决的问题 [1]",
        "prior_work": "前人工作",
        "method_innovation": "方法与创新 [1]",
        "limitations": "论文未明确说明",
    }
    base.update(overrides)
    return base


def _patch_llm(monkeypatch, client: FakeLLMClient) -> FakeLLMClient:
    monkeypatch.setattr("app.api.v1.endpoints.papers.get_llm_client", lambda: client)
    return client


def _summary_client(monkeypatch, payload: dict[str, str]) -> FakeLLMClient:
    return _patch_llm(
        monkeypatch,
        FakeLLMClient(responses=[final_response(json.dumps(payload, ensure_ascii=False))]),
    )


async def test_summaries_require_login(client: AsyncClient) -> None:
    assert (await client.get(f"{PAPERS}/1/summaries")).status_code == 401
    assert (await client.post(f"{PAPERS}/1/summaries/generate", json={})).status_code == 401


async def test_summary_not_generated_returns_404(reader_client: AsyncClient) -> None:
    paper = await _paper()

    response = await reader_client.get(f"{PAPERS}/{paper.id}/summaries")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "summary_not_generated"


async def test_summary_paper_not_ready_hides_paper(reader_client: AsyncClient) -> None:
    paper = await _paper(status=PaperStatus.parsing.value)

    response = await reader_client.get(f"{PAPERS}/{paper.id}/summaries")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "paper_not_found"


async def test_generate_summary_and_cache(reader_client: AsyncClient, monkeypatch) -> None:
    paper = await _paper(contents=("transformer 方法的原文片段", "实验与不足的原文"))
    fake = _summary_client(monkeypatch, _payload())

    response = await reader_client.post(
        f"{PAPERS}/{paper.id}/summaries/generate",
        json={"level": "standard"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["paper_id"] == paper.id
    assert body["level"] == "standard"
    assert body["content"] == {
        "problem": "要解决的问题 [1]",
        "prior_work": "前人工作",
        "method_innovation": "方法与创新 [1]",
        "limitations": "论文未明确说明",
    }
    assert body["generated_at"].endswith("Z")
    assert [citation["index"] for citation in body["citations"]] == [1]
    assert body["citations"][0]["paper_id"] == paper.id
    assert len(fake.calls) == 1

    cached = await reader_client.get(f"{PAPERS}/{paper.id}/summaries")
    again = await reader_client.post(f"{PAPERS}/{paper.id}/summaries/generate", json={})

    assert cached.status_code == 200
    assert cached.json()["content"] == body["content"]
    assert again.status_code == 200
    assert again.json()["generated_at"] == body["generated_at"]
    assert len(fake.calls) == 1  # 缓存命中不再调用模型


async def test_force_regenerate_bypasses_cache(reader_client: AsyncClient, monkeypatch) -> None:
    """force=true 跳过缓存、重新生成并覆盖同一缓存键（契约 §8.5）。"""
    paper = await _paper(contents=("transformer 方法的原文片段",))
    fake = _patch_llm(
        monkeypatch,
        FakeLLMClient(
            responses=[
                final_response(json.dumps(_payload(problem="第一次生成 [1]"), ensure_ascii=False)),
                final_response(json.dumps(_payload(problem="重新生成 [1]"), ensure_ascii=False)),
            ]
        ),
    )

    first = await reader_client.post(
        f"{PAPERS}/{paper.id}/summaries/generate", json={"level": "standard"}
    )
    assert first.status_code == 200
    assert first.json()["content"]["problem"] == "第一次生成 [1]"
    assert len(fake.calls) == 1

    forced = await reader_client.post(
        f"{PAPERS}/{paper.id}/summaries/generate",
        json={"level": "standard", "force": True},
    )
    assert forced.status_code == 200
    assert forced.json()["content"]["problem"] == "重新生成 [1]"
    assert len(fake.calls) == 2  # 跳过缓存，确实调用了模型

    cached = await reader_client.get(f"{PAPERS}/{paper.id}/summaries?level=standard")
    assert cached.status_code == 200
    assert cached.json()["content"]["problem"] == "重新生成 [1]"  # 缓存已被覆盖
    assert cached.json()["generated_at"] == forced.json()["generated_at"]

    again = await reader_client.post(
        f"{PAPERS}/{paper.id}/summaries/generate", json={"level": "standard"}
    )
    assert again.status_code == 200
    assert again.json()["content"]["problem"] == "重新生成 [1]"
    assert len(fake.calls) == 2  # 不带 force 仍然命中缓存


async def test_summary_levels_are_independent(reader_client: AsyncClient, monkeypatch) -> None:
    paper = await _paper()
    _summary_client(monkeypatch, _payload())

    first = await reader_client.post(
        f"{PAPERS}/{paper.id}/summaries/generate",
        json={"level": "brief"},
    )
    missing = await reader_client.get(
        f"{PAPERS}/{paper.id}/summaries",
        params={"level": "deep"},
    )

    assert first.status_code == 200
    assert first.json()["level"] == "brief"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "summary_not_generated"
    assert (
        await reader_client.get(f"{PAPERS}/{paper.id}/summaries", params={"level": "bogus"})
    ).status_code == 422


async def test_summary_of_archived_paper(reader_client: AsyncClient, monkeypatch) -> None:
    paper = await _paper(archived=True)
    _summary_client(monkeypatch, _payload())

    response = await reader_client.post(f"{PAPERS}/{paper.id}/summaries/generate", json={})

    assert response.status_code == 200
    assert response.json()["level"] == "standard"


async def test_summary_requires_chunks(reader_client: AsyncClient, monkeypatch) -> None:
    paper = await _paper(contents=())
    _summary_client(monkeypatch, _payload())

    response = await reader_client.post(f"{PAPERS}/{paper.id}/summaries/generate", json={})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "paper_not_ready"


async def test_summary_llm_unavailable(reader_client: AsyncClient, monkeypatch) -> None:
    paper = await _paper()

    def _raise() -> None:
        raise AppError(503, "llm_unavailable", "AI 服务未配置或暂不可用")

    monkeypatch.setattr("app.api.v1.endpoints.papers.get_llm_client", _raise)

    response = await reader_client.post(f"{PAPERS}/{paper.id}/summaries/generate", json={})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"


@pytest.mark.parametrize(
    "raw",
    ["", "这不是 JSON", "[1, 2, 3]", '{"problem": "只写了一部分"'],
)
async def test_summary_invalid_model_output(
    reader_client: AsyncClient,
    monkeypatch,
    raw: str,
) -> None:
    paper = await _paper()
    _patch_llm(monkeypatch, FakeLLMClient(responses=[final_response(raw)]))

    response = await reader_client.post(f"{PAPERS}/{paper.id}/summaries/generate", json={})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_failed"


async def test_summary_missing_fields_use_fallback(
    reader_client: AsyncClient,
    monkeypatch,
) -> None:
    paper = await _paper()
    _summary_client(monkeypatch, {"problem": "问题描述"})

    response = await reader_client.post(f"{PAPERS}/{paper.id}/summaries/generate", json={})

    assert response.status_code == 200
    content = response.json()["content"]
    assert content["problem"] == "问题描述"
    assert content["prior_work"] == "论文未明确说明"
    assert content["method_innovation"] == "论文未明确说明"
    assert content["limitations"] == "论文未明确说明"


async def test_summary_citations_are_renumbered(reader_client: AsyncClient, monkeypatch) -> None:
    paper = await _paper(contents=("第一段原文", "第二段原文包含关键实验"))
    _summary_client(
        monkeypatch,
        _payload(problem="问题描述 [2]", method_innovation="方法 [2]", prior_work="前人工作"),
    )

    response = await reader_client.post(f"{PAPERS}/{paper.id}/summaries/generate", json={})

    body = response.json()
    assert [citation["index"] for citation in body["citations"]] == [1]
    assert body["citations"][0]["page"] == 2
    assert body["content"]["problem"] == "问题描述 [1]"


async def test_available_summary_levels_in_paper_detail(
    reader_client: AsyncClient,
    monkeypatch,
) -> None:
    paper = await _paper()
    before = (await reader_client.get(f"{PAPERS}/{paper.id}")).json()
    _summary_client(monkeypatch, _payload())

    await reader_client.post(f"{PAPERS}/{paper.id}/summaries/generate", json={"level": "brief"})
    after = (await reader_client.get(f"{PAPERS}/{paper.id}")).json()

    assert before["available_summary_levels"] == []
    assert after["available_summary_levels"] == ["brief"]
