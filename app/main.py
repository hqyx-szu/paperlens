import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db import reset_engine
from app.services.paper_processing import recover_stale_papers

logger = logging.getLogger("paperlens.startup")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings())
    try:
        await recover_stale_papers()
    except Exception:
        logger.warning("failed to recover stale papers", exc_info=True)
    yield
    await reset_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    admin_ui = Path(__file__).resolve().parent.parent / "web" / "admin" / "index.html"
    app_ui = Path(__file__).resolve().parent.parent / "web" / "app" / "index.html"

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "admin": "/admin",
        }

    if admin_ui.is_file():

        @app.get("/admin", include_in_schema=False)
        @app.get("/admin/", include_in_schema=False)
        @app.get("/login", include_in_schema=False)
        async def admin_page() -> FileResponse:
            return FileResponse(admin_ui)

    if app_ui.is_file():

        @app.get("/app", include_in_schema=False)
        @app.get("/app/", include_in_schema=False)
        async def app_page() -> FileResponse:
            return FileResponse(app_ui)

        # 产品设计 §4.1 的页面地图用路径形式；页面本身是 hash 路由，这里做别名跳转。
        # 只声明显式路由（int 约束），避免 catch-all 抢走 /api、/docs 与静态资源。
        @app.get("/sessions/{session_id:int}", include_in_schema=False)
        async def session_alias(session_id: int) -> RedirectResponse:
            return RedirectResponse(f"/app#/sessions/{session_id}", status_code=307)

        @app.get("/repos/{repo_id:int}", include_in_schema=False)
        async def repo_alias(repo_id: int) -> RedirectResponse:
            return RedirectResponse(f"/app#/repos/{repo_id}", status_code=307)

        @app.get("/papers/{paper_id:int}", include_in_schema=False)
        async def paper_alias(paper_id: int) -> RedirectResponse:
            return RedirectResponse(f"/app#/papers/{paper_id}", status_code=307)

    if admin_ui.is_file():

        @app.get("/admin/{view}", include_in_schema=False)
        async def admin_view_alias(
            view: Literal["users", "papers", "repos", "overview"],
        ) -> RedirectResponse:
            return RedirectResponse(f"/admin#/{view}", status_code=307)

    return app


app = create_app()
