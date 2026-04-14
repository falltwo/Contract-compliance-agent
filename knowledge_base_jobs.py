from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv


def _jobs_path() -> Path:
    load_dotenv()
    return Path(os.getenv("KNOWLEDGE_BASE_JOBS_PATH", "data/knowledge_base/jobs.jsonl"))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append_job(entry: dict[str, Any]) -> dict[str, Any]:
    path = _jobs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def start_job(*, job_type: str, source_name: str) -> dict[str, Any]:
    entry = {
        "job_id": f"kbjob-{uuid4().hex[:12]}",
        "job_type": job_type,
        "source_name": source_name,
        "started_at": _now_iso(),
        "finished_at": None,
        "status": "running",
        "records_fetched": 0,
        "records_inserted": 0,
        "records_updated": 0,
        "records_deleted": 0,
        "error_message": "",
    }
    return _append_job(entry)


def finish_job(
    job: dict[str, Any],
    *,
    status: str,
    records_fetched: int = 0,
    records_inserted: int = 0,
    records_updated: int = 0,
    records_deleted: int = 0,
    error_message: str = "",
) -> dict[str, Any]:
    completed = {
        **job,
        "finished_at": _now_iso(),
        "status": status,
        "records_fetched": records_fetched,
        "records_inserted": records_inserted,
        "records_updated": records_updated,
        "records_deleted": records_deleted,
        "error_message": error_message,
    }
    return _append_job(completed)


def load_jobs(*, limit: int = 200, job_type: str | None = None) -> list[dict[str, Any]]:
    path = _jobs_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if job_type and item.get("job_type") != job_type:
                continue
            rows.append(item)
    return rows[-limit:]

