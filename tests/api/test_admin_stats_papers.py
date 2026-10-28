from httpx import AsyncClient

from app.core.papers import PaperStatus
from app.db import get_session_factory
from app.models import Paper


async def _create_paper(
    *,
    status: str,
    archived: bool = False,
) -> Paper:
    async with get_session_factory()() as db:
        paper = Paper(title=f"{status}-{archived}", authors=[], status=status)
        paper.is_archived = archived
        db.add(paper)
        await db.commit()
        await db.refresh(paper)
        return paper


async def test_admin_stats_counts_papers(admin_client: AsyncClient) -> None:
    empty = await admin_client.get("/api/v1/admin/stats")
    assert empty.status_code == 200
    assert empty.json()["papers_total"] == 0
    assert empty.json()["repos_total"] == 0

    await _create_paper(status=PaperStatus.ready.value)
    await _create_paper(status=PaperStatus.ready.value, archived=True)
    await _create_paper(status=PaperStatus.failed.value)
    await _create_paper(status=PaperStatus.failed.value, archived=True)
    await _create_paper(status=PaperStatus.queued.value)
    await _create_paper(status=PaperStatus.parsing.value)
    await _create_paper(status=PaperStatus.indexing.value, archived=True)

    response = await admin_client.get("/api/v1/admin/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["papers_total"] == 7
    assert body["papers_ready"] == 2
    assert body["papers_processing"] == 2
    assert body["papers_failed"] == 1
    assert body["papers_archived"] == 1
    assert body["repos_total"] == 0
    assert body["repos_private"] == 0


async def test_openapi_contains_d2_paths(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    for path in (
        "/api/v1/admin/papers/import",
        "/api/v1/admin/papers",
        "/api/v1/papers",
        "/api/v1/papers/{paper_id}/download",
    ):
        assert path in paths


async def test_import_route_is_import_not_id(admin_client: AsyncClient) -> None:
    response = await admin_client.post("/api/v1/admin/papers/import")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
