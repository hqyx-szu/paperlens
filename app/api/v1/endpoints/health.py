import logging

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db import get_engine

logger = logging.getLogger("paperlens.health")

router = APIRouter()


class LiveHealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ReadyHealthResponse(BaseModel):
    status: str
    database: str


@router.get(
    "/health/live",
    response_model=LiveHealthResponse,
    summary="Liveness probe",
)
async def live_health() -> LiveHealthResponse:
    settings = get_settings()
    return LiveHealthResponse(
        status="ok",
        service="paperlens",
        version=settings.app_version,
    )


@router.get(
    "/health/ready",
    response_model=ReadyHealthResponse,
    summary="Readiness probe",
)
async def ready_health() -> ReadyHealthResponse:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness check failed")
        raise AppError(503, "database_unavailable", "数据库暂不可用") from None

    return ReadyHealthResponse(status="ok", database="ok")
