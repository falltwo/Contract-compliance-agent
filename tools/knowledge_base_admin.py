from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from knowledge_base_jobs import finish_job, load_jobs, start_job
from knowledge_base_providers import (
    ProviderFetchError,
    fetch_provider_records,
    list_provider_specs,
    provider_details,
)
from knowledge_base_policy import SYNC_POLICY, all_dataset_health, dataset_health
from knowledge_base_sync import (
    all_dataset_stats,
    dataset_stats,
    ingest_dataset,
    sync_records,
    sync_records_from_file,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _seed_path(dataset: str) -> Path:
    base = _project_root() / "data" / "knowledge_base"
    if dataset == "laws":
        return base / "laws_seed.json"
    if dataset == "cases":
        return base / "cases_seed.json"
    raise ValueError(f"Unsupported dataset: {dataset}")


def cmd_sync_seed(args: argparse.Namespace) -> int:
    result = sync_records_from_file(
        dataset=args.dataset,
        source_name=f"local_{args.dataset}_seed",
        file_path=_seed_path(args.dataset),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_sync_file(args: argparse.Namespace) -> int:
    result = sync_records_from_file(
        dataset=args.dataset,
        source_name=args.source_name or Path(args.file).name,
        file_path=args.file,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    result = ingest_dataset(args.dataset)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    result = dataset_stats(args.dataset)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    rows = load_jobs(limit=args.limit, job_type=args.job_type)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_list_providers(args: argparse.Namespace) -> int:
    print(json.dumps(list_provider_specs(), ensure_ascii=False, indent=2))
    return 0


def cmd_provider_info(args: argparse.Namespace) -> int:
    print(json.dumps(provider_details(args.provider_id), ensure_ascii=False, indent=2))
    return 0


def cmd_sync_provider(args: argparse.Namespace) -> int:
    provider_job = start_job(job_type="provider_fetch", source_name=args.provider_id)
    try:
        fetch_result = fetch_provider_records(args.provider_id)
        finish_job(
            provider_job,
            status="success",
            records_fetched=len(fetch_result.records),
        )
        result = sync_records(
            dataset=fetch_result.spec.dataset,
            source_name=fetch_result.spec.source_name,
            records=fetch_result.records,
        )
        print(json.dumps({"provider": fetch_result.spec.provider_id, **result}, ensure_ascii=False, indent=2))
        return 0
    except ProviderFetchError as e:
        finish_job(
            provider_job,
            status="failed",
            error_message=str(e),
        )
        print(
            json.dumps(
                {
                    "provider": getattr(e, "provider_id", args.provider_id),
                    "status": "failed",
                    "error": str(e),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


def cmd_sync_all_seeds(args: argparse.Namespace) -> int:
    results = []
    for dataset in ("laws", "cases"):
        results.append(
            sync_records_from_file(
                dataset=dataset,
                source_name=f"local_{dataset}_seed",
                file_path=_seed_path(dataset),
            )
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def cmd_ingest_all(args: argparse.Namespace) -> int:
    results = []
    for dataset in ("laws", "cases"):
        results.append(ingest_dataset(dataset))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def cmd_status_all(args: argparse.Namespace) -> int:
    print(json.dumps(all_dataset_stats(), ensure_ascii=False, indent=2))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    result = dataset_health(args.dataset)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_health_all(args: argparse.Namespace) -> int:
    print(json.dumps(all_dataset_health(), ensure_ascii=False, indent=2))
    return 0


def _default_provider_for_dataset(dataset: str) -> str:
    env_name = f"KNOWLEDGE_BASE_{dataset.upper()}_DEFAULT_PROVIDER"
    configured = os.getenv(env_name, "").strip()
    if configured:
        return configured
    return "judicial_laws_official" if dataset == "laws" else "judgments_official"


def _maintenance_plan_for_dataset(dataset: str) -> dict[str, object]:
    health = dataset_health(dataset)
    provider_id = _default_provider_for_dataset(dataset)
    plan_steps: list[dict[str, object]] = []

    if health["sync_due"]:
        plan_steps.append(
            {
                "action": "sync_provider",
                "provider_id": provider_id,
                "reason": f"{dataset} source refresh is due",
            }
        )
    if health["ingest_due"]:
        plan_steps.append(
            {
                "action": "ingest_dataset",
                "dataset": dataset,
                "reason": f"{dataset} index rebuild is due",
            }
        )

    return {
        "dataset": dataset,
        "policy": SYNC_POLICY[dataset],
        "health": health,
        "default_provider": provider_id,
        "steps": plan_steps,
    }


def cmd_maintenance_plan(args: argparse.Namespace) -> int:
    datasets = [args.dataset] if args.dataset else ["laws", "cases"]
    result = [_maintenance_plan_for_dataset(dataset) for dataset in datasets]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_run_maintenance(args: argparse.Namespace) -> int:
    datasets = [args.dataset] if args.dataset else ["laws", "cases"]
    execution_results: list[dict[str, object]] = []

    for dataset in datasets:
        plan = _maintenance_plan_for_dataset(dataset)
        step_results: list[dict[str, object]] = []
        for step in plan["steps"]:
            action = str(step["action"])
            if action == "sync_provider":
                provider_id = str(step["provider_id"])
                provider_job = start_job(job_type="provider_fetch", source_name=provider_id)
                try:
                    fetch_result = fetch_provider_records(provider_id)
                    finish_job(
                        provider_job,
                        status="success",
                        records_fetched=len(fetch_result.records),
                    )
                    sync_result = sync_records(
                        dataset=fetch_result.spec.dataset,
                        source_name=fetch_result.spec.source_name,
                        records=fetch_result.records,
                    )
                    step_results.append(
                        {
                            "action": "sync_provider",
                            "provider_id": provider_id,
                            "status": "success",
                            "result": sync_result,
                        }
                    )
                except ProviderFetchError as e:
                    finish_job(provider_job, status="failed", error_message=str(e))
                    step_results.append(
                        {
                            "action": "sync_provider",
                            "provider_id": provider_id,
                            "status": "failed",
                            "error": str(e),
                        }
                    )
                    if args.stop_on_error:
                        execution_results.append({"dataset": dataset, "plan": plan, "steps": step_results})
                        print(json.dumps(execution_results, ensure_ascii=False, indent=2))
                        return 1
            elif action == "ingest_dataset":
                try:
                    ingest_result = ingest_dataset(dataset)
                    step_results.append(
                        {
                            "action": "ingest_dataset",
                            "dataset": dataset,
                            "status": "success",
                            "result": ingest_result,
                        }
                    )
                except Exception as e:
                    step_results.append(
                        {
                            "action": "ingest_dataset",
                            "dataset": dataset,
                            "status": "failed",
                            "error": str(e),
                        }
                    )
                    if args.stop_on_error:
                        execution_results.append({"dataset": dataset, "plan": plan, "steps": step_results})
                        print(json.dumps(execution_results, ensure_ascii=False, indent=2))
                        return 1
        execution_results.append({"dataset": dataset, "plan": plan, "steps": step_results})

    print(json.dumps(execution_results, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Knowledge base backend admin")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_seed = sub.add_parser("sync-seed", help="Sync local seed dataset into knowledge base storage")
    sync_seed.add_argument("dataset", choices=["laws", "cases"])
    sync_seed.set_defaults(func=cmd_sync_seed)

    sync_all_seeds = sub.add_parser("sync-all-seeds", help="Sync all built-in seed datasets")
    sync_all_seeds.set_defaults(func=cmd_sync_all_seeds)

    list_providers = sub.add_parser("list-providers", help="List available backend knowledge base providers")
    list_providers.set_defaults(func=cmd_list_providers)

    provider_info = sub.add_parser("provider-info", help="Show one provider configuration")
    provider_info.add_argument("provider_id")
    provider_info.set_defaults(func=cmd_provider_info)

    sync_provider = sub.add_parser("sync-provider", help="Sync one provider into knowledge base storage")
    sync_provider.add_argument("provider_id")
    sync_provider.set_defaults(func=cmd_sync_provider)

    sync_file = sub.add_parser("sync-file", help="Sync external JSON file into knowledge base storage")
    sync_file.add_argument("dataset", choices=["laws", "cases"])
    sync_file.add_argument("file")
    sync_file.add_argument("--source-name", default="")
    sync_file.set_defaults(func=cmd_sync_file)

    ingest = sub.add_parser("ingest", help="Ingest saved knowledge base dataset into existing RAG index")
    ingest.add_argument("dataset", choices=["laws", "cases"])
    ingest.set_defaults(func=cmd_ingest)

    ingest_all = sub.add_parser("ingest-all", help="Ingest all saved datasets into existing RAG index")
    ingest_all.set_defaults(func=cmd_ingest_all)

    status = sub.add_parser("status", help="Show dataset status")
    status.add_argument("dataset", choices=["laws", "cases"])
    status.set_defaults(func=cmd_status)

    status_all = sub.add_parser("status-all", help="Show all dataset status")
    status_all.set_defaults(func=cmd_status_all)

    health = sub.add_parser("health", help="Show dataset freshness and recommended maintenance actions")
    health.add_argument("dataset", choices=["laws", "cases"])
    health.set_defaults(func=cmd_health)

    health_all = sub.add_parser("health-all", help="Show freshness and recommended actions for all datasets")
    health_all.set_defaults(func=cmd_health_all)

    maintenance_plan = sub.add_parser(
        "maintenance-plan",
        help="Show the maintenance steps that would run based on dataset health",
    )
    maintenance_plan.add_argument("dataset", nargs="?", choices=["laws", "cases"])
    maintenance_plan.set_defaults(func=cmd_maintenance_plan)

    run_maintenance = sub.add_parser(
        "run-maintenance",
        help="Run due sync and ingest actions based on dataset health",
    )
    run_maintenance.add_argument("dataset", nargs="?", choices=["laws", "cases"])
    run_maintenance.add_argument("--stop-on-error", action="store_true")
    run_maintenance.set_defaults(func=cmd_run_maintenance)

    jobs = sub.add_parser("jobs", help="Show recent sync jobs")
    jobs.add_argument("--limit", type=int, default=20)
    jobs.add_argument("--job-type", default=None)
    jobs.set_defaults(func=cmd_jobs)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
