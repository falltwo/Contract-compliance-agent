"""sources_registry 單元測試（使用暫存檔案）。"""
import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_registry_file():
    """提供暫存的 registry 檔案路徑，測試後清理。"""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def test_load_registry_empty(monkeypatch, temp_registry_file):
    """不存在或空檔案時應回傳空列表。"""
    from sources_registry import load_registry

    # 指向不存在的檔案 → 應回傳 []
    nonexistent = Path(temp_registry_file).parent / "nonexistent_registry_12345.json"
    assert not nonexistent.exists()
    monkeypatch.setenv("SOURCES_REGISTRY_PATH", str(nonexistent))
    assert load_registry() == []


def test_save_and_load_registry(monkeypatch, temp_registry_file):
    """寫入後讀取應一致。"""
    monkeypatch.setenv("SOURCES_REGISTRY_PATH", temp_registry_file)

    from sources_registry import load_registry, save_registry, update_registry_on_ingest

    assert load_registry() == []

    save_registry([{"source": "a.txt", "chunk_count": 10}])
    entries = load_registry()
    assert len(entries) == 1
    assert entries[0]["source"] == "a.txt"
    assert entries[0]["chunk_count"] == 10

    update_registry_on_ingest([{"source": "b.md", "chunk_count": 5, "chat_id": None}])
    entries = load_registry()
    assert len(entries) == 2
    by_src = {e["source"]: e for e in entries}
    assert by_src["a.txt"]["chunk_count"] == 10
    assert by_src["b.md"]["chunk_count"] == 5


def test_update_registry_overwrite_same_source(monkeypatch, temp_registry_file):
    """同 source 再次 ingest 應覆寫 chunk_count。"""
    monkeypatch.setenv("SOURCES_REGISTRY_PATH", temp_registry_file)

    from sources_registry import load_registry, update_registry_on_ingest

    update_registry_on_ingest([{"source": "x.pdf", "chunk_count": 3}])
    update_registry_on_ingest([{"source": "x.pdf", "chunk_count": 7}])
    entries = load_registry()
    assert len(entries) == 1
    assert entries[0]["chunk_count"] == 7


def test_list_sources_filter_by_chat_id(monkeypatch, temp_registry_file):
    """list_sources(chat_id=...) 只回傳該對話的來源。"""
    monkeypatch.setenv("SOURCES_REGISTRY_PATH", temp_registry_file)

    from sources_registry import list_sources, update_registry_on_ingest

    update_registry_on_ingest([
        {"source": "uploaded/chat-1/a.txt", "chunk_count": 2, "chat_id": "chat-1"},
        {"source": "uploaded/chat-2/b.txt", "chunk_count": 3, "chat_id": "chat-2"},
    ])
    all_entries = list_sources()
    assert len(all_entries) == 2
    chat1 = list_sources(chat_id="chat-1")
    assert len(chat1) == 1
    assert chat1[0]["source"] == "uploaded/chat-1/a.txt"
