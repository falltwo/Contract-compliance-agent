"""與 Streamlit／FastAPI 共用的 Agent 問答入口：薄封裝 `agent_router.route_and_answer`，並可選寫入 Eval 日誌。"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from agent_router import route_and_answer
from eval_log import is_enabled as eval_log_enabled
from eval_log import log_run as eval_log_run

logger = logging.getLogger(__name__)

_TIMEOUT_MSG = (
    "後端分析逾時，可能是法條查詢、外部搜尋或模型回應卡住。請重新分析，或改用較小範圍的問題再試一次。"
)


def _is_timeout_exc(exc: BaseException) -> bool:
    """判斷是否為任何類型的 timeout 例外（futures / openai / httpx / requests）。"""
    type_name = type(exc).__name__
    if "Timeout" in type_name or "timeout" in type_name:
        return True
    module = getattr(type(exc), "__module__", "") or ""
    if any(m in module for m in ("openai", "httpx", "requests", "urllib")):
        return True
    return False


def _route_and_answer_with_timeout(
    **kwargs: Any,
) -> tuple[str, list[str], list[dict[str, Any]], str, dict[str, Any] | None]:
    timeout_sec = float(os.getenv("CHAT_ROUTE_TIMEOUT_SEC", "40").strip() or "40")
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(route_and_answer, **kwargs)
    try:
        return future.result(timeout=timeout_sec)
    except FuturesTimeoutError:
        future.cancel()
        logger.warning("route_and_answer timed out after %.1fs (route-level)", timeout_sec)
        return (_TIMEOUT_MSG, [], [], "backend_timeout", {"timed_out": True, "timeout_sec": timeout_sec})
    except Exception as exc:
        future.cancel()
        if _is_timeout_exc(exc):
            logger.warning("route_and_answer raised timeout exception: %s: %s", type(exc).__name__, exc)
            return (_TIMEOUT_MSG, [], [], "backend_timeout", {"timed_out": True, "timeout_sec": timeout_sec})
        logger.error("route_and_answer raised unexpected exception: %s", exc, exc_info=True)
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def answer_with_rag(
    *,
    question: str,
    top_k: int,
    history: list[dict[str, Any]] | None = None,
    strict: bool = True,
    chat_id: str | None = None,
    rag_scope_chat_id: str | None = None,
    original_question: str | None = None,
    clarification_reply: str | None = None,
    chart_confirmation_question: str | None = None,
    chart_confirmation_reply: str | None = None,
) -> tuple[str, list[str], list[dict[str, Any]], str, dict[str, Any] | None]:
    """走總管 Agent，回傳 (answer, sources, chunks, tool_name, extra)。"""
    answer, sources, chunks, tool_name, extra = _route_and_answer_with_timeout(
        question=question,
        top_k=top_k,
        history=history or [],
        strict=strict,
        chat_id=chat_id,
        rag_scope_chat_id=rag_scope_chat_id,
        original_question=original_question,
        clarification_reply=clarification_reply,
        chart_confirmation_question=chart_confirmation_question,
        chart_confirmation_reply=chart_confirmation_reply,
    )
    return answer, sources, chunks, tool_name, extra


def answer_with_rag_and_log(
    *,
    question: str,
    top_k: int,
    history: list[dict[str, Any]] | None = None,
    strict: bool = True,
    chat_id: str | None = None,
    rag_scope_chat_id: str | None = None,
    original_question: str | None = None,
    clarification_reply: str | None = None,
    chart_confirmation_question: str | None = None,
    chart_confirmation_reply: str | None = None,
) -> tuple[str, list[str], list[dict[str, Any]], str, dict[str, Any] | None]:
    """呼叫 answer_with_rag 並計時；若啟用 Eval 記錄則寫入一筆 log（與原 streamlit_app 行為一致）。"""
    t0 = time.perf_counter()
    answer, sources, chunks, tool_name, extra = answer_with_rag(
        question=question,
        top_k=top_k,
        history=history or [],
        strict=strict,
        chat_id=chat_id,
        rag_scope_chat_id=rag_scope_chat_id,
        original_question=original_question,
        clarification_reply=clarification_reply,
        chart_confirmation_question=chart_confirmation_question,
        chart_confirmation_reply=chart_confirmation_reply,
    )
    latency = time.perf_counter() - t0
    if eval_log_enabled():
        eval_log_run(
            question=question,
            answer=answer or "",
            tool_name=tool_name,
            latency_sec=latency,
            top_k=top_k,
            source_count=len(sources),
            chat_id=chat_id,
        )
    return answer, sources, chunks, tool_name, extra
