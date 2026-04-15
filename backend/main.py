"""
FastAPI 應用程式入口。

啟動（專案根目錄）：
  uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

與 Streamlit 雙軌並存時，另開終端機：
  uv run streamlit run streamlit_app.py
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.routes.chat import router as chat_router
from backend.api.routes.admin import router as admin_router
from backend.api.routes.eval import router as eval_router
from backend.api.routes.health import router as health_router
from backend.api.routes.ingest import router as ingest_router
from backend.api.routes.stub import router as stub_router
from backend.config import get_settings
from backend.exception_handlers import (
    http_exception_handler,
    pydantic_validation_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.api_title, version=settings.api_version)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_origin_regex=settings.api_cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(ValidationError, pydantic_validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(admin_router)
    app.include_router(ingest_router)
    app.include_router(eval_router)
    app.include_router(stub_router)

    return app


app = create_app()
