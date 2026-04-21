"""Pipeline 進度回饋：以 ContextVar 為傳遞介面，讓深層函式不必修改簽章也能
將階段訊息推送到上層（例如 SSE streaming endpoint）。

用法：
    # 呼叫端（例如 chat_stream worker）：
    from progress import set_progress_emitter
    def emit(stage, message): event_queue.put(("status", {"stage": stage, "message": message}))
    set_progress_emitter(emit)

    # 任意深層程式：
    from progress import emit_progress
    emit_progress("law_search", "搜尋民法第188條…")
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ProgressEmitter = Callable[[str, str], None]

_progress_emitter: ContextVar[Optional[ProgressEmitter]] = ContextVar(
    "progress_emitter", default=None
)


def set_progress_emitter(emitter: ProgressEmitter | None) -> None:
    """安裝一個 emitter；呼叫端負責在結束時清除（一般用 reset token）。"""
    _progress_emitter.set(emitter)


def emit_progress(stage: str, message: str) -> None:
    """從任意深度推送一則進度訊息；若未安裝 emitter 則靜默忽略。"""
    emitter = _progress_emitter.get()
    if emitter is None:
        return
    try:
        emitter(stage, message)
    except Exception as e:  # 進度回饋失敗不應中斷主流程
        logger.debug("progress emitter failed: %s", e)
