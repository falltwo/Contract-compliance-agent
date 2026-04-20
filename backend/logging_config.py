"""集中化 backend logging 設定。

在 `create_app()` 開頭呼叫 `configure_logging()` 一次，以後各模組只需：

    import logging
    logger = logging.getLogger(__name__)

即可輸出帶模組名稱、時間戳與 traceback 的結構化 log 至 stderr（被 journald 收走）。

環境變數：
- `LOG_LEVEL`   預設 INFO，可設 DEBUG/WARNING/ERROR
- `LOG_FORMAT`  預設「人類可讀」；設 `json` 以後升級結構化 log 時可切換
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def configure_logging() -> None:
    """冪等地設定 root logger。重複呼叫不會增加 handler。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    # 清掉前人（uvicorn / streamlit）已經掛上的 root handler，避免重複輸出
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)

    # 調低第三方吵雜 logger（可視情況放行）
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _CONFIGURED = True
