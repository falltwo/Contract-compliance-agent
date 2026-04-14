import os
import tempfile


def test_sync_law_records_insert_and_update(monkeypatch):
    fd_data, data_path = tempfile.mkstemp(suffix=".json")
    fd_jobs, jobs_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd_data)
    os.close(fd_jobs)
    try:
        monkeypatch.setenv("KNOWLEDGE_BASE_LAWS_PATH", data_path)
        monkeypatch.setenv("KNOWLEDGE_BASE_JOBS_PATH", jobs_path)
        from knowledge_base_sync import load_dataset, sync_records

        first = sync_records(
            dataset="laws",
            source_name="judicial",
            records=[
                {
                    "law_name": "民法",
                    "article_no": "第184條",
                    "article_text": "因故意或過失，不法侵害他人之權利者，負損害賠償責任。",
                    "source_url": "https://example.test/civil/184",
                }
            ],
        )
        assert first["records_inserted"] == 1
        assert first["records_updated"] == 0

        second = sync_records(
            dataset="laws",
            source_name="judicial",
            records=[
                {
                    "law_name": "民法",
                    "article_no": "第184條",
                    "article_text": "因故意或過失，不法侵害他人之權利者，負損害賠償責任。情節重大者亦同。",
                    "source_url": "https://example.test/civil/184",
                }
            ],
        )
        assert second["records_inserted"] == 0
        assert second["records_updated"] == 1
        saved = load_dataset("laws")
        assert len(saved) == 1
        assert saved[0]["law_id"] == "民法::第184條"
    finally:
        for path in (data_path, jobs_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def test_sync_case_records_insert(monkeypatch):
    fd_data, data_path = tempfile.mkstemp(suffix=".json")
    fd_jobs, jobs_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd_data)
    os.close(fd_jobs)
    try:
        monkeypatch.setenv("KNOWLEDGE_BASE_CASES_PATH", data_path)
        monkeypatch.setenv("KNOWLEDGE_BASE_JOBS_PATH", jobs_path)
        from knowledge_base_sync import load_dataset, sync_records

        result = sync_records(
            dataset="cases",
            source_name="judgment_system",
            records=[
                {
                    "case_number": "112年度台上字第123號",
                    "court_name": "最高法院",
                    "judgment_date": "2024-01-01",
                    "summary": "契約解除爭議。",
                    "full_text": "本件爭點在於契約解除是否合法。",
                    "source_url": "https://example.test/case/123",
                }
            ],
        )
        assert result["records_inserted"] == 1
        saved = load_dataset("cases")
        assert len(saved) == 1
        assert saved[0]["case_id"] == "最高法院::112年度台上字第123號"
    finally:
        for path in (data_path, jobs_path):
            try:
                os.unlink(path)
            except OSError:
                pass

