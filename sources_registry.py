"""輕量來源註冊表：記錄知識庫中每個 source 及其 chunk 數量，供 list_sources 使用。"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _registry_path() -> Path:
    load_dotenv()
    path = os.getenv("SOURCES_REGISTRY_PATH", "sources_registry.json")
    return Path(path)


def load_registry() -> list[dict]:
    """讀取註冊表，回傳 list of { source, chunk_count, chat_id? }。"""
    p = _registry_path()
    if not p.exists():
        return []
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        entries = data.get("entries")
        if not isinstance(entries, list):
            return []
        return [e for e in entries if isinstance(e, dict) and "source" in e]
    except Exception as e:
        logger.warning("load_registry failed for %s: %s", p, e, exc_info=True)
        return []


def save_registry(entries: list[dict]) -> None:
    """寫入註冊表。"""
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")


def update_registry_on_ingest(new_entries: list[dict]) -> None:
    """將本次 ingest 的來源合併進註冊表（同 source 覆寫 chunk_count）。"""
    entries = load_registry()
    by_source: dict[str, dict] = {e["source"]: {**e} for e in entries}
    for e in new_entries:
        s = e.get("source")
        if not s:
            continue
        by_source[s] = {
            "source": s,
            "chunk_count": int(e.get("chunk_count", 0)),
            "chat_id": e.get("chat_id"),
        }
    save_registry(list(by_source.values()))


def list_sources(chat_id: str | None = None) -> list[dict]:
    """列出知識庫來源；可選依 chat_id 篩選（僅保留該對話上傳的）。"""
    entries = load_registry()
    if chat_id is not None:
        entries = [e for e in entries if e.get("chat_id") == chat_id]
    return entries


def delete_source_from_registry(source: str, chat_id: str | None = None) -> bool:
    """從註冊表刪除指定 source（可選限定 chat_id）。回傳是否有實際刪除。"""
    entries = load_registry()
    before = len(entries)
    entries = [
        e for e in entries
        if not (
            e.get("source") == source
            and (chat_id is None or e.get("chat_id") == chat_id)
        )
    ]
    if len(entries) < before:
        save_registry(entries)
        return True
    return False
