from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from knowledge_base_jobs import load_jobs
from knowledge_base_sync import DATASET_CONFIG, dataset_stats


SYNC_POLICY: dict[str, dict[str, int]] = {
    "laws": {
        "sync_interval_days": 7,
        "ingest_interval_days": 7,
    },
    "cases": {
        "sync_interval_days": 14,
        "ingest_interval_days": 14,
    },
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _latest_successful_job(*, job_type: str) -> dict[str, Any] | None:
    jobs = load_jobs(limit=1000, job_type=job_type)
    successful = [job for job in jobs if job.get("status") == "success"]
    if not successful:
        return None
    successful.sort(key=lambda job: str(job.get("finished_at") or job.get("started_at") or ""))
    return successful[-1]


def _is_due(last_at: datetime | None, *, interval_days: int) -> bool:
    if last_at is None:
        return True
    return datetime.now() - last_at >= timedelta(days=interval_days)


def dataset_health(dataset: str) -> dict[str, Any]:
    if dataset not in DATASET_CONFIG:
        raise ValueError(f"Unsupported dataset: {dataset}")

    stats = dataset_stats(dataset)
    policy = SYNC_POLICY[dataset]
    sync_job = _latest_successful_job(job_type=f"sync_{dataset}")
    ingest_job = _latest_successful_job(job_type=f"ingest_{dataset}")

    dataset_updated_at = (
        datetime.fromtimestamp(stats["updated_at"]).isoformat(timespec="seconds")
        if stats.get("updated_at")
        else None
    )
    last_sync_at = None if sync_job is None else sync_job.get("finished_at") or sync_job.get("started_at")
    last_ingest_at = None if ingest_job is None else ingest_job.get("finished_at") or ingest_job.get("started_at")

    sync_due = _is_due(_parse_iso(last_sync_at), interval_days=policy["sync_interval_days"])
    ingest_due = _is_due(_parse_iso(last_ingest_at), interval_days=policy["ingest_interval_days"])

    recommended_actions: list[str] = []
    if stats["record_count"] == 0:
        recommended_actions.append(f"Run dataset sync for {dataset} before ingesting.")
    if sync_due:
        recommended_actions.append(
            f"Refresh {dataset} source data. Sync interval target is {policy['sync_interval_days']} days."
        )
    if ingest_due:
        recommended_actions.append(
            f"Rebuild {dataset} index. Ingest interval target is {policy['ingest_interval_days']} days."
        )

    if stats["record_count"] == 0:
        overall_status = "empty"
    elif sync_due or ingest_due:
        overall_status = "stale"
    else:
        overall_status = "healthy"

    return {
        "dataset": dataset,
        "record_count": stats["record_count"],
        "dataset_path": stats["dataset_path"],
        "dataset_updated_at": dataset_updated_at,
        "last_successful_sync_at": last_sync_at,
        "last_successful_ingest_at": last_ingest_at,
        "sync_interval_days": policy["sync_interval_days"],
        "ingest_interval_days": policy["ingest_interval_days"],
        "sync_due": sync_due,
        "ingest_due": ingest_due,
        "overall_status": overall_status,
        "recommended_actions": recommended_actions,
    }


def all_dataset_health() -> list[dict[str, Any]]:
    return [dataset_health(dataset) for dataset in DATASET_CONFIG.keys()]
