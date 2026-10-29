from __future__ import annotations

from httpx import AsyncClient

from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import Paper, PaperChunk, RepoStar
from tests.api.conftest import login

REPOS = "/api/v1/repos"
RELATED = f"{REPOS}/related"


async def _client_for(make_client, user) -> AsyncClient:
    client = await make_client()
    await login(client, user.email)
    return client


async def _paper_with_chunk(title: str, content: str) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(title=title, authors=["A"], status=PaperStatus.ready.value)
        db.add(paper)
        await db.flush()
        db.add(
            PaperChunk(
                paper_id=paper.id,
                chunk_index=0,
                page=1,
                content=content,
                char_count=len(content),
            )
        )
        await db.commit()
        await db.refresh(paper)
        return paper


async def _create_repo(client: AsyncClient, name: str, **extra: object) -> dict:
    payload: dict[str, object] = {"name": name, **extra}
    response = await client.post(REPOS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _add_paper(client: AsyncClient, repo_id: int, paper_id: int) -> None:
    response = await client.post(f"{REPOS}/{repo_id}/papers", json={"paper_ids": [paper_id]})
    assert response.status_code == 200, response.text


async def _session(client: AsyncClient, query: str) -> dict:
    response = await client.post("/api/v1/search", json={"query": query})
    assert response.status_code == 201, response.text
    return response.json()


async def test_related_requires_login(client: AsyncClient) -> None:
    assert (await client.get(RELATED, params={"session_id": 1})).status_code == 401


async def test_related_session_id_required(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)

    assert (await client.get(RELATED)).status_code == 422
    assert (await client.get(RELATED, params={"session_id": 0})).status_code == 422


async def test_related_other_user_session_is_hidden(
    make_client,
    reader_user,
    user_factory,
) -> None:
    owner = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    reader = await _client_for(make_client, other)
    await _paper_with_chunk("Transformer", "transformer attention evidence")
    session = await _session(owner, "transformer")

    response = await reader.get(RELATED, params={"session_id": session["id"]})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_related_empty_evidence_returns_empty_page(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)
    session = await _session(client, "zzz-no-such-keyword")

    assert session["has_evidence"] is False
    body = (await client.get(RELATED, params={"session_id": session["id"]})).json()

    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}
    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["related_repo_count"] == 0


async def test_related_filters_private_own_and_reports_reason(
    make_client,
    reader_user,
    user_factory,
) -> None:
    me = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    other_client = await _client_for(make_client, other)
    await _paper_with_chunk("Transformer survey", "transformer attention evidence")
    session = await _session(me, "transformer")

    matched_paper = await _paper_with_chunk("Transformer models", "transformer variants")
    matched_repo = await _create_repo(
        other_client,
        "Transformer 综述",
        description="关于 transformer 的整理",
        visibility="public",
    )
    await _add_paper(other_client, matched_repo["id"], matched_paper.id)

    await _create_repo(other_client, "菜谱", description="做饭", visibility="public")
    private_repo = await _create_repo(other_client, "transformer 私有", description="transformer")
    await _create_repo(me, "transformer 我的", description="transformer", visibility="public")

    body = (await me.get(RELATED, params={"session_id": session["id"]})).json()

    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == matched_repo["id"]
    assert item["name"] == "Transformer 综述"
    assert item["owner"] == {"id": other.id, "display_name": other.display_name}
    assert item["paper_count"] == 1
    assert item["star_count"] == 0
    assert item["is_starred"] is False
    assert item["reason"]
    assert len(item["reason"]) <= 100
    assert "transformer" in item["reason"].lower()
    assert private_repo["id"] != item["id"]


async def test_related_ignores_unrelated_popular_repo(
    make_client,
    reader_user,
    user_factory,
) -> None:
    me = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    other_client = await _client_for(make_client, other)
    await _paper_with_chunk("Transformer survey", "transformer attention evidence")
    session = await _session(me, "transformer")
    popular = await _create_repo(
        other_client, "做饭大全", description="家庭菜谱", visibility="public"
    )

    for index in range(5):
        fan = await user_factory(email=f"fan{index}@example.com", display_name=f"Fan{index}")
        async with get_session_factory()() as db:
            db.add(RepoStar(repo_id=popular["id"], user_id=fan.id))
            await db.commit()

    body = (await me.get(RELATED, params={"session_id": session["id"]})).json()

    assert body["items"] == []
    assert body["total"] == 0


async def test_related_relevance_beats_star_count(make_client, reader_user, user_factory) -> None:
    me = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    other_client = await _client_for(make_client, other)
    await _paper_with_chunk("Transformer survey", "transformer attention evidence")
    session = await _session(me, "transformer")

    relevant = await _create_repo(
        other_client,
        "transformer",
        description="transformer survey 整理",
        visibility="public",
    )
    relevant_paper = await _paper_with_chunk("transformer survey", "transformer survey details")
    await _add_paper(other_client, relevant["id"], relevant_paper.id)
    weak = await _create_repo(other_client, "杂项", description="一些整理", visibility="public")
    weak_paper = await _paper_with_chunk("transformer 细节", "transformer details")
    await _add_paper(other_client, weak["id"], weak_paper.id)

    for index in range(3):
        fan = await user_factory(email=f"fan{index}@example.com", display_name=f"Fan{index}")
        async with get_session_factory()() as db:
            db.add(RepoStar(repo_id=weak["id"], user_id=fan.id))
            await db.commit()

    body = (await me.get(RELATED, params={"session_id": session["id"]})).json()

    assert [item["id"] for item in body["items"]] == [relevant["id"], weak["id"]]
    assert body["items"][1]["star_count"] == 3
    assert body["items"][0]["star_count"] == 0


async def test_related_pagination_and_count_consistency(
    make_client,
    reader_user,
    user_factory,
) -> None:
    me = await _client_for(make_client, reader_user)
    other = await user_factory(email="other@example.com", display_name="Other")
    other_client = await _client_for(make_client, other)
    await _paper_with_chunk("Transformer survey", "transformer attention evidence")
    session = await _session(me, "transformer")
    for index in range(3):
        await _create_repo(other_client, f"transformer {index}", visibility="public")

    first = (await me.get(RELATED, params={"session_id": session["id"], "page_size": 2})).json()
    second = (
        await me.get(RELATED, params={"session_id": session["id"], "page": 2, "page_size": 2})
    ).json()
    detail = (await me.get(f"/api/v1/sessions/{session['id']}")).json()

    assert first["total"] == 3
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert detail["related_repo_count"] == 3
    assert len({item["id"] for item in first["items"] + second["items"]}) == 3


async def test_related_route_is_not_captured_by_repo_id(make_client, reader_user) -> None:
    client = await _client_for(make_client, reader_user)

    response = await client.get(RELATED, params={"session_id": 1})

    # 路由未被 /{repo_id} 吞掉：返回业务 404（会话不存在），不是路径参数 422。
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
