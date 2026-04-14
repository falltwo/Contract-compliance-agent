import json
import os
import tempfile
from pathlib import Path


def test_maintenance_plan_includes_sync_and_ingest_when_due(monkeypatch):
    fd_data, data_path = tempfile.mkstemp(suffix=".json")
    fd_jobs, jobs_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd_data)
    os.close(fd_jobs)
    try:
        Path(data_path).write_text("[]", encoding="utf-8")
        Path(jobs_path).write_text("", encoding="utf-8")
        monkeypatch.setenv("KNOWLEDGE_BASE_LAWS_PATH", data_path)
        monkeypatch.setenv("KNOWLEDGE_BASE_JOBS_PATH", jobs_path)

        from tools.knowledge_base_admin import _maintenance_plan_for_dataset

        result = _maintenance_plan_for_dataset("laws")
        actions = [step["action"] for step in result["steps"]]
        assert "sync_provider" in actions
        assert "ingest_dataset" in actions
        assert result["default_provider"] == "judicial_laws_official"
    finally:
        for path in (data_path, jobs_path):
            try:
                os.unlink(path)
            except OSError:
                pass
