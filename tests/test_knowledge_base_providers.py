import json
import os
import tempfile
from pathlib import Path


def test_official_provider_reads_snapshot(monkeypatch):
    fd, snapshot_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        Path(snapshot_path).write_text(
            json.dumps(
                [
                    {
                        "law_name": "Civil Code",
                        "article_no": "Article 184",
                        "article_text": "A person who intentionally or negligently infringes another right is liable.",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("JUDICIAL_LAWS_SNAPSHOT_PATH", snapshot_path)

        from knowledge_base_providers import fetch_provider_records

        result = fetch_provider_records("judicial_laws_official")
        assert result.spec.provider_id == "judicial_laws_official"
        assert len(result.records) == 1
        assert result.metadata["source_path"] == snapshot_path
    finally:
        try:
            os.unlink(snapshot_path)
        except OSError:
            pass
