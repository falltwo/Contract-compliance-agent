"""SSE streaming chat endpoint.

將原本同步阻塞的 /api/v1/chat 包裝成 SSE stream，讓前端在 LLM 開始生成時
就能逐步收到文字片段（token），大幅降低使用者感知延遲。

協議：
  POST /api/v1/chat/stream  (body = ChatRequest JSON)
  Response: text/event-stream
    event: status    data: {"stage": "...", "message": "..."}  ← 階段進度
    event: token     data: {"t": "部分文"}       ← 增量文字
    event: meta      data: {"sources": [...], "chunks": [...], ...}  ← 最終 metadata
    event: done      data: {}                    ← 結束
    event: error     data: {"message": "..."}    ← 錯誤
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import threading
import queue
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend.schemas.chat import ChatMessage, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


def _history_to_payload(history: list[ChatMessage]) -> list[dict[str, Any]]:
    return [{"role": m.role, "content": m.content} for m in history]


def _sse_event(event: str, data: dict[str, Any] | str) -> str:
    """Format a single SSE event."""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _run_pipeline_in_thread(
    body: ChatRequest,
    event_queue: queue.Queue,
    cancel_event: threading.Event,
) -> None:
    """在子執行緒中跑 pipeline，透過 queue 回傳事件。"""
    try:
        from chat_service import answer_with_rag_and_log
        from progress import set_progress_emitter

        # 安裝進度 emitter，讓深層函式（如 contract+law 路徑）可直接推送狀態
        def _emit(stage: str, message: str) -> None:
            if cancel_event.is_set():
                return
            event_queue.put(("status", {"stage": stage, "message": message}))

        set_progress_emitter(_emit)

        history_payload = _history_to_payload(body.history)

        t0 = time.perf_counter()

        answer, sources, chunks_raw, tool_name, extra = answer_with_rag_and_log(
            question=body.message.strip(),
            top_k=body.top_k,
            history=history_payload,
            strict=body.strict,
            chat_id=body.chat_id,
            rag_scope_chat_id=body.rag_scope_chat_id,
            original_question=body.original_question,
            clarification_reply=body.clarification_reply,
            chart_confirmation_question=body.chart_confirmation_question,
            chart_confirmation_reply=body.chart_confirmation_reply,
            cancel_event=cancel_event,
        )
        latency_sec = time.perf_counter() - t0

        # If cancelled during pipeline, stop silently
        if cancel_event.is_set():
            event_queue.put(("done", {}))
            return

        answer = answer or ""

        event_queue.put(("status", {"stage": "streaming", "message": "正在輸出回答..."}))

        # 分塊串流回答
        chunk_size = int(os.getenv("STREAM_CHUNK_SIZE", "60"))
        for i in range(0, len(answer), chunk_size):
            if cancel_event.is_set():
                break
            fragment = answer[i: i + chunk_size]
            event_queue.put(("token", {"t": fragment}))

        if cancel_event.is_set():
            event_queue.put(("done", {}))
            return

        # Metadata
        cleaned_chunks = []
        for c in (chunks_raw or []):
            if isinstance(c, dict):
                cleaned_chunks.append({"tag": c.get("tag", ""), "text": c.get("text", "")})

        next_orig = None
        next_chart_q = None
        if tool_name == "ask_web_vs_rag":
            next_orig = body.message.strip()
        if tool_name == "analyze_and_chart" and extra and extra.get("asked_chart_confirmation"):
            next_chart_q = (extra.get("chart_query") or body.message.strip()) or None

        meta: dict[str, Any] = {
            "sources": list(sources or []),
            "chunks": cleaned_chunks,
            "tool_name": tool_name,
            "extra": extra,
            "latency_sec": round(latency_sec, 4),
            "next_original_question_for_clarification": next_orig,
            "next_chart_confirmation_question": next_chart_q,
        }
        event_queue.put(("meta", meta))
        event_queue.put(("done", {}))

    except Exception as exc:
        logger.exception("stream_chat pipeline error")
        event_queue.put(("error", {"message": str(exc)[:500]}))


async def _stream_chat(body: ChatRequest, request: Request) -> AsyncGenerator[str, None]:
    """Run the chat pipeline in a thread and yield SSE events from the queue."""

    # 立即發送 status 讓前端知道連線成功
    yield _sse_event("status", {"stage": "routing", "message": "正在分析問題類型..."})

    event_q: queue.Queue = queue.Queue()
    cancel_event = threading.Event()

    # 啟動子執行緒跑 pipeline
    worker = threading.Thread(
        target=_run_pipeline_in_thread,
        args=(body, event_q, cancel_event),
        daemon=True,
    )
    worker.start()

    # 主協程從 queue 讀取事件並 yield SSE；同時每次循環檢查客戶端是否斷線
    heartbeat_interval = 15.0
    last_heartbeat = time.monotonic()

    while True:
        # 偵測前端斷線（使用者按停止或關閉頁面）
        if await request.is_disconnected():
            logger.info("Client disconnected — setting cancel_event to stop worker")
            cancel_event.set()
            break

        # 非阻塞地嘗試從 queue 取出事件
        try:
            event_type, data = event_q.get_nowait()
            yield _sse_event(event_type, data)
            if event_type in ("done", "error"):
                break
        except queue.Empty:
            # 沒有事件，檢查是否需要送 heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                yield ": heartbeat\n\n"
                last_heartbeat = now
                if not worker.is_alive():
                    yield _sse_event("error", {"message": "backend worker unexpectedly stopped"})
                    break
            # 讓出事件循環，避免 busy-wait
            await asyncio.sleep(0.05)

    worker.join(timeout=2.0)


@router.post("/chat/stream")
async def post_chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """SSE streaming chat endpoint — 與 /chat 相同邏輯但透過 SSE 逐步回傳。"""
    return StreamingResponse(
        _stream_chat(body, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 不要 buffer
        },
    )
