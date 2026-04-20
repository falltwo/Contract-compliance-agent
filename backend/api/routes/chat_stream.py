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

import json
import logging
import os
import time
import threading
import queue
from typing import Any, Generator

from fastapi import APIRouter
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
) -> None:
    """在子執行緒中跑 pipeline，透過 queue 回傳事件。"""
    try:
        from chat_service import answer_with_rag_and_log

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
        )
        latency_sec = time.perf_counter() - t0
        answer = answer or ""

        event_queue.put(("status", {"stage": "streaming", "message": "正在輸出回答..."}))

        # 分塊串流回答
        chunk_size = int(os.getenv("STREAM_CHUNK_SIZE", "60"))
        for i in range(0, len(answer), chunk_size):
            fragment = answer[i: i + chunk_size]
            event_queue.put(("token", {"t": fragment}))

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


def _stream_chat(body: ChatRequest) -> Generator[str, None, None]:
    """Run the chat pipeline in a thread and yield SSE events from the queue."""

    # 立即發送 status 讓前端知道連線成功
    yield _sse_event("status", {"stage": "routing", "message": "正在分析問題類型..."})

    event_q: queue.Queue = queue.Queue()

    # 啟動子執行緒跑 pipeline
    worker = threading.Thread(
        target=_run_pipeline_in_thread,
        args=(body, event_q),
        daemon=True,
    )
    worker.start()

    # 主執行緒持續從 queue 讀取事件並 yield SSE
    # 設置 heartbeat 間隔防止連線中斷
    heartbeat_interval = 15.0  # 秒
    last_event_time = time.monotonic()

    while True:
        try:
            event_type, data = event_q.get(timeout=heartbeat_interval)
            last_event_time = time.monotonic()
            yield _sse_event(event_type, data)

            if event_type in ("done", "error"):
                break
        except queue.Empty:
            # 超過 heartbeat_interval 沒有事件，送一個 comment 保持連線
            yield ": heartbeat\n\n"
            # 如果 worker 死了，結束
            if not worker.is_alive():
                yield _sse_event("error", {"message": "backend worker unexpectedly stopped"})
                break

    worker.join(timeout=2.0)


@router.post("/chat/stream")
def post_chat_stream(body: ChatRequest) -> StreamingResponse:
    """SSE streaming chat endpoint — 與 /chat 相同邏輯但透過 SSE 逐步回傳。"""
    return StreamingResponse(
        _stream_chat(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 不要 buffer
        },
    )
