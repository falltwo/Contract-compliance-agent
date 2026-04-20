from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.schemas.common import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


def _error_payload(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return body.model_dump()


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # StarletteHTTPException.detail 可能是 str 或結構化
    detail = exc.detail
    message = detail if isinstance(detail, str) else str(detail)
    details = None if isinstance(detail, str) else detail
    # 4xx 用 info，5xx 用 warning（含完整 traceback）
    if exc.status_code >= 500:
        logger.warning(
            "HTTPException %s on %s %s: %s",
            exc.status_code,
            request.method,
            request.url.path,
            message,
            exc_info=True,
        )
    else:
        logger.info(
            "HTTPException %s on %s %s: %s",
            exc.status_code,
            request.method,
            request.url.path,
            message,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(code="HTTP_ERROR", message=message, details=details),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.info(
        "RequestValidationError on %s %s: %s errors",
        request.method,
        request.url.path,
        len(exc.errors()),
    )
    return JSONResponse(
        status_code=422,
        content=_error_payload(
            code="VALIDATION_ERROR",
            message="請求參數驗證失敗",
            details=exc.errors(),
        ),
    )


async def pydantic_validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    logger.info(
        "Pydantic ValidationError on %s %s: %s errors",
        request.method,
        request.url.path,
        len(exc.errors()),
    )
    return JSONResponse(
        status_code=422,
        content=_error_payload(
            code="VALIDATION_ERROR",
            message="資料驗證失敗",
            details=exc.errors(),
        ),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # 不將內部例外字串回給前端（避免洩漏路徑／金鑰）；但伺服器日誌必須完整保留 traceback
    logger.error(
        "Unhandled %s on %s %s: %s",
        type(exc).__name__,
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content=_error_payload(
            code="INTERNAL_ERROR",
            message="伺服器發生未預期錯誤",
            details=None,
        ),
    )
