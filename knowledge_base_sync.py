from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from knowledge_base_jobs import finish_job, start_job
from rag_ingest import IngestRecord, build_chunks_from_records, ingest_chunks


DATASET_CONFIG = {
    "laws": {
        "path_env": "KNOWLEDGE_BASE_LAWS_PATH",
        "default_path": "data/knowledge_base/laws.json",
        "id_field": "law_id",
    },
    "cases": {
        "path_env": "KNOWLEDGE_BASE_CASES_PATH",
        "default_path": "data/knowledge_base/cases.json",
        "id_field": "case_id",
    },
}


def _dataset_path(dataset: str) -> Path:
    if dataset not in DATASET_CONFIG:
        raise ValueError(f"Unsupported dataset: {dataset}")
    load_dotenv()
    cfg = DATASET_CONFIG[dataset]
    return Path(os.getenv(cfg["path_env"], cfg["default_path"]))


def _id_field(dataset: str) -> str:
    return str(DATASET_CONFIG[dataset]["id_field"])


def compute_content_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_law_record(record: dict[str, Any], *, source_name: str) -> dict[str, Any]:
    law_name = str(record.get("law_name", "")).strip()
    article_no = str(record.get("article_no", "")).strip()
    article_text = str(record.get("article_text", "")).strip()
    if not law_name or not article_no or not article_text:
        raise ValueError("law_name, article_no, article_text are required")
    payload = {
        "law_name": law_name,
        "article_no": article_no,
        "article_text": article_text,
        "effective_status": str(record.get("effective_status", "active")).strip() or "active",
        "amended_at": str(record.get("amended_at", "")).strip(),
        "source_url": str(record.get("source_url", "")).strip(),
        "source_name": source_name,
        "fetched_at": str(record.get("fetched_at", "")).strip(),
    }
    law_id = str(record.get("law_id", "")).strip() or f"{law_name}::{article_no}"
    return {
        "law_id": law_id,
        **payload,
        "content_hash": compute_content_hash(payload),
    }


def normalize_case_record(record: dict[str, Any], *, source_name: str) -> dict[str, Any]:
    case_number = str(record.get("case_number", "")).strip()
    court_name = str(record.get("court_name", "")).strip()
    full_text = str(record.get("full_text", "")).strip()
    if not case_number or not court_name or not full_text:
        raise ValueError("case_number, court_name, full_text are required")
    payload = {
        "case_number": case_number,
        "court_name": court_name,
        "judgment_date": str(record.get("judgment_date", "")).strip(),
        "case_type": str(record.get("case_type", "")).strip(),
        "summary": str(record.get("summary", "")).strip(),
        "full_text": full_text,
        "source_url": str(record.get("source_url", "")).strip(),
        "source_name": source_name,
        "fetched_at": str(record.get("fetched_at", "")).strip(),
    }
    case_id = str(record.get("case_id", "")).strip() or f"{court_name}::{case_number}"
    return {
        "case_id": case_id,
        **payload,
        "content_hash": compute_content_hash(payload),
    }


def load_dataset(dataset: str) -> list[dict[str, Any]]:
    path = _dataset_path(dataset)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_dataset(dataset: str, records: list[dict[str, Any]]) -> None:
    path = _dataset_path(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_records_from_file(*, dataset: str, source_name: str, file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("sync source file must contain a JSON array")
    return sync_records(dataset=dataset, source_name=source_name, records=raw)


def sync_records_from_json_text(*, dataset: str, source_name: str, json_text: str) -> dict[str, Any]:
    raw = json.loads(json_text)
    if not isinstance(raw, list):
        raise ValueError("sync source content must contain a JSON array")
    return sync_records(dataset=dataset, source_name=source_name, records=raw)


def sync_records(
    *,
    dataset: str,
    source_name: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    if dataset not in DATASET_CONFIG:
        raise ValueError(f"Unsupported dataset: {dataset}")
    job = start_job(job_type=f"sync_{dataset}", source_name=source_name)
    try:
        normalizer = normalize_law_record if dataset == "laws" else normalize_case_record
        id_field = _id_field(dataset)
        incoming = [normalizer(record, source_name=source_name) for record in records]
        existing = load_dataset(dataset)
        existing_by_id = {str(item[id_field]): item for item in existing if item.get(id_field)}

        inserted = 0
        updated = 0
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for item in incoming:
            record_id = str(item[id_field])
            seen_ids.add(record_id)
            current = existing_by_id.get(record_id)
            if current is None:
                inserted += 1
            elif current.get("content_hash") != item.get("content_hash"):
                updated += 1
            merged.append(item)

        deleted = 0
        for item in existing:
            record_id = str(item.get(id_field, ""))
            if not record_id:
                continue
            if record_id not in seen_ids:
                deleted += 1

        save_dataset(dataset, merged)
        result = {
            "dataset": dataset,
            "source_name": source_name,
            "records_fetched": len(records),
            "records_inserted": inserted,
            "records_updated": updated,
            "records_deleted": deleted,
            "records_saved": len(merged),
        }
        finish_job(
            job,
            status="success",
            records_fetched=result["records_fetched"],
            records_inserted=inserted,
            records_updated=updated,
            records_deleted=deleted,
        )
        return result
    except Exception as e:
        finish_job(job, status="failed", records_fetched=len(records), error_message=str(e))
        raise


def build_ingest_records(dataset: str) -> list[IngestRecord]:
    records = load_dataset(dataset)
    ingest_records: list[IngestRecord] = []
    if dataset == "laws":
        for item in records:
            ingest_records.append(
                IngestRecord(
                    source=f"laws/{item['law_name']}/{item['article_no']}",
                    text=item["article_text"],
                    metadata_extra={
                        "kb_dataset": "laws",
                        "law_id": item["law_id"],
                        "law_name": item["law_name"],
                        "article_no": item["article_no"],
                        "source_url": item.get("source_url", ""),
                    },
                )
            )
    elif dataset == "cases":
        for item in records:
            text = (item.get("summary", "") + "\n\n" + item["full_text"]).strip()
            ingest_records.append(
                IngestRecord(
                    source=f"cases/{item['court_name']}/{item['case_number']}",
                    text=text,
                    metadata_extra={
                        "kb_dataset": "cases",
                        "case_id": item["case_id"],
                        "case_number": item["case_number"],
                        "court_name": item["court_name"],
                        "source_url": item.get("source_url", ""),
                    },
                )
            )
    else:
        raise ValueError(f"Unsupported dataset: {dataset}")
    return ingest_records


def ingest_dataset(dataset: str) -> dict[str, Any]:
    job = start_job(job_type=f"ingest_{dataset}", source_name=dataset)
    try:
        ingest_records = build_ingest_records(dataset)
        chunks = build_chunks_from_records(ingest_records)
        result = ingest_chunks(chunks, chat_id=None)
        finish_job(
            job,
            status="success",
            records_fetched=len(ingest_records),
            records_inserted=result.get("upserted", 0),
        )
        return {
            "dataset": dataset,
            "records": len(ingest_records),
            **result,
        }
    except Exception as e:
        finish_job(job, status="failed", error_message=str(e))
        raise


def dataset_stats(dataset: str) -> dict[str, Any]:
    records = load_dataset(dataset)
    path = _dataset_path(dataset)
    if dataset == "laws":
        unique_sources = sorted({item.get("law_name", "") for item in records if item.get("law_name")})
    elif dataset == "cases":
        unique_sources = sorted({item.get("court_name", "") for item in records if item.get("court_name")})
    else:
        unique_sources = []
    return {
        "dataset": dataset,
        "record_count": len(records),
        "source_groups": unique_sources[:10],
        "dataset_path": str(path),
        "updated_at": path.stat().st_mtime if path.exists() else None,
    }


def all_dataset_stats() -> list[dict[str, Any]]:
    return [dataset_stats(dataset) for dataset in DATASET_CONFIG.keys()]
