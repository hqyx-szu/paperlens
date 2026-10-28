import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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

    return app


app = create_app()
