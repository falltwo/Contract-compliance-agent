import os
import tempfile


def test_start_and_finish_job(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        monkeypatch.setenv("KNOWLEDGE_BASE_JOBS_PATH", path)
        from knowledge_base_jobs import finish_job, load_jobs, start_job

        job = start_job(job_type="sync_laws", source_name="judicial")
        done = finish_job(
            job,
            status="success",
            records_fetched=10,
            records_inserted=8,
            records_updated=2,
        )
        jobs = load_jobs()
        assert len(jobs) == 2
        assert jobs[0]["status"] == "running"
        assert done["status"] == "success"
        assert jobs[-1]["records_fetched"] == 10
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

