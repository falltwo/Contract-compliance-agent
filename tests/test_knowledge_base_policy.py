import json
import os
import tempfile
from pathlib import Path


def test_dataset_health_marks_empty_dataset_as_due(monkeypatch):
    fd_data, data_path = tempfile.mkstemp(suffix=".json")
    fd_jobs, jobs_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd_data)
    os.close(fd_jobs)
    try:
        Path(data_path).write_text("[]", encoding="utf-8")
        Path(jobs_path).write_text("", encoding="utf-8")
        monkeypatch.setenv("KNOWLEDGE_BASE_LAWS_PATH", data_path)
        monkeypatch.setenv("KNOWLEDGE_BASE_JOBS_PATH", jobs_path)

        from knowledge_base_policy import dataset_health

        result = dataset_health("laws")
        assert result["overall_status"] == "empty"
        assert result["sync_due"] is True
        assert result["ingest_due"] is True
        assert result["recommended_actions"]
    finally:
        for path in (data_path, jobs_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def test_ingest_dataset_writes_success_job(monkeypatch):
    fd_data, data_path = tempfile.mkstemp(suffix=".json")
    fd_jobs, jobs_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd_data)
    os.close(fd_jobs)
    try:
        monkeypatch.setenv("KNOWLEDGE_BASE_LAWS_PATH", data_path)
        monkeypatch.setenv("KNOWLEDGE_BASE_JOBS_PATH", jobs_path)

        sample_records = [
            {
                "law_id": "CivilCode::184",
                "law_name": "Civil Code",
                "article_no": "Article 184",
                "article_text": "A person who intentionally or negligently infringes the rights of another is liable.",
                "effective_status": "active",
                "amended_at": "",
                "source_url": "https://example.test/civil/184",
                "source_name": "test_seed",
                "fetched_at": "",
                "content_hash": "hash-1",
            }
        ]
        Path(data_path).write_text(json.dumps(sample_records, ensure_ascii=False), encoding="utf-8")

        import knowledge_base_sync

        monkeypatch.setattr(
            knowledge_base_sync,
            "ingest_chunks",
            lambda chunks, chat_id=None: {
                "chunks": len(chunks),
                "upserted": len(chunks),
            },
        )

        result = knowledge_base_sync.ingest_dataset("laws")
        assert result["dataset"] == "laws"
        assert result["records"] == 1

        rows = Path(jobs_path).read_text(encoding="utf-8").strip().splitlines()
        assert rows
        parsed = [json.loads(line) for line in rows if line.strip()]
        completed = [row for row in parsed if row["job_type"] == "ingest_laws" and row["status"] == "success"]
        assert completed
        assert completed[-1]["records_fetched"] == 1
    finally:
        for path in (data_path, jobs_path):
            try:
                os.unlink(path)
            except OSError:
                pass
