"""Eval 運行記錄：寫入每次問答的 question / answer / tool / latency 等，供 dashboard 查閱。

需設 EVAL_LOG_ENABLED=1 才會寫入；日誌路徑由 EVAL_LOG_PATH 指定，預設 eval_runs.jsonl。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def _path() -> Path:
    load_dotenv()
    p = os.getenv("EVAL_LOG_PATH", "eval_runs.jsonl")
    return Path(p)


def is_enabled() -> bool:
    """是否啟用 Eval 記錄（EVAL_LOG_ENABLED=1 時才寫入）。"""
    load_dotenv()
    return os.getenv("EVAL_LOG_ENABLED", "").strip().lower() in ("1", "true", "yes")


def log_run(
    *,
    question: str,
    answer: str,
    tool_name: str,
    latency_sec: float,
    top_k: int,
    source_count: int,
    chat_id: str | None = None,
    timestamp: str | None = None,
) -> None:
    """寫入一筆運行記錄（JSONL 一行）。"""
    if not is_enabled():
        return
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    record = {
        "timestamp": ts,
        "question": question,
        "answer": (answer or "")[:5000],  # 截斷過長答案
        "tool_name": tool_name,
        "latency_sec": round(latency_sec, 3),
        "top_k": top_k,
        "source_count": source_count,
        "chat_id": chat_id,
    }
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_runs(limit: int = 500) -> list[dict]:
    """讀取最近 limit 筆記錄（新到舊）。若檔案不存在或為空則回傳 []。"""
    path = _path()
    if not path.exists():
        return []
    lines: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)
    records: list[dict] = []
    for line in reversed(lines[-limit:]):
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records
