from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("paperlens.error")


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})


_DEFAULT_MESSAGES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "请求不合法"),
    401: ("unauthorized", "请先登录"),
    403: ("forbidden", "无权访问"),
    404: ("not_found", "资源不存在"),
    405: ("method_not_allowed", "请求方法不允许"),
    409: ("conflict", "资源冲突"),
    429: ("too_many_requests", "请求过于频繁，请稍后重试"),
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        logger.warning("application error", extra={"code": exc.code})
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return error_response(422, "validation_error", "请求参数不合法", details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _DEFAULT_MESSAGES.get(
            exc.status_code,
            ("http_error", "请求处理失败"),
        )
        if exc.status_code >= 500:
            message = "服务暂时不可用，请稍后重试"
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", exc_info=exc)
        return error_response(500, "internal_error", "服务暂时不可用，请稍后重试")
