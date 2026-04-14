from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    dataset: str
    source_name: str
    description: str
    mode: str
    enabled: bool = True
    env_keys: tuple[str, ...] = ()
    default_url: str = ""
    notes: str = ""
    supports_production: bool = False


@dataclass(frozen=True)
class ProviderFetchResult:
    spec: ProviderSpec
    records: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderFetchError(RuntimeError):
    def __init__(self, provider_id: str, message: str):
        super().__init__(message)
        self.provider_id = provider_id


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _seed_file(name: str) -> Path:
    return _project_root() / "data" / "knowledge_base" / name


PROVIDERS: dict[str, ProviderSpec] = {
    "laws_seed": ProviderSpec(
        provider_id="laws_seed",
        dataset="laws",
        source_name="local_laws_seed",
        description="Local seed laws dataset for backend testing and bootstrap",
        mode="local_seed",
        notes="Use for local bootstrap only.",
    ),
    "cases_seed": ProviderSpec(
        provider_id="cases_seed",
        dataset="cases",
        source_name="local_cases_seed",
        description="Local seed cases dataset for backend testing and bootstrap",
        mode="local_seed",
        notes="Use for local bootstrap only.",
    ),
    "judicial_laws_official": ProviderSpec(
        provider_id="judicial_laws_official",
        dataset="laws",
        source_name="judicial_laws_official",
        description="Official Judicial Yuan laws provider",
        mode="official_snapshot",
        env_keys=("JUDICIAL_LAWS_SNAPSHOT_PATH", "JUDICIAL_LAWS_SOURCE_URL"),
        default_url="https://law.judicial.gov.tw/",
        notes=(
            "Configure a local snapshot JSON path first. "
            "Later this provider can be wired to an HTTP fetcher without changing the CLI contract."
        ),
        supports_production=True,
    ),
    "judgments_official": ProviderSpec(
        provider_id="judgments_official",
        dataset="cases",
        source_name="judgments_official",
        description="Official judgments provider",
        mode="official_snapshot",
        env_keys=("JUDGMENTS_SNAPSHOT_PATH", "JUDGMENTS_SOURCE_URL"),
        default_url="https://judgment.judicial.gov.tw/",
        notes=(
            "Configure a local snapshot JSON path first. "
            "Later this provider can be wired to an HTTP fetcher without changing the CLI contract."
        ),
        supports_production=True,
    ),
    "judicial_laws_placeholder": ProviderSpec(
        provider_id="judicial_laws_placeholder",
        dataset="laws",
        source_name="judicial_laws_placeholder",
        description="Placeholder adapter for future Judicial Yuan laws synchronization",
        mode="placeholder",
        notes="Deprecated placeholder. Prefer judicial_laws_official.",
    ),
    "judgments_placeholder": ProviderSpec(
        provider_id="judgments_placeholder",
        dataset="cases",
        source_name="judgments_placeholder",
        description="Placeholder adapter for future judgment database synchronization",
        mode="placeholder",
        notes="Deprecated placeholder. Prefer judgments_official.",
    ),
}


def _spec_to_dict(spec: ProviderSpec) -> dict[str, Any]:
    load_dotenv()
    data = asdict(spec)
    data["configured_env"] = {
        key: bool(os.getenv(key))
        for key in spec.env_keys
    }
    return data


def list_provider_specs() -> list[dict[str, Any]]:
    return [_spec_to_dict(spec) for spec in PROVIDERS.values()]


def get_provider_spec(provider_id: str) -> ProviderSpec:
    if provider_id not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider_id}")
    return PROVIDERS[provider_id]


def provider_details(provider_id: str) -> dict[str, Any]:
    return _spec_to_dict(get_provider_spec(provider_id))


def _read_json_array(path: Path, *, provider_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise ProviderFetchError(provider_id, f"Configured snapshot file does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProviderFetchError(provider_id, f"Snapshot file is not valid JSON: {path}") from e
    if not isinstance(data, list):
        raise ProviderFetchError(provider_id, f"Provider {provider_id} source must contain a JSON array")
    return data


def _fetch_local_seed(spec: ProviderSpec) -> ProviderFetchResult:
    path = _seed_file("laws_seed.json" if spec.dataset == "laws" else "cases_seed.json")
    data = _read_json_array(path, provider_id=spec.provider_id)
    return ProviderFetchResult(spec=spec, records=data, metadata={"source_path": str(path)})


def _fetch_official_snapshot(spec: ProviderSpec) -> ProviderFetchResult:
    load_dotenv()
    snapshot_env = spec.env_keys[0] if spec.env_keys else ""
    source_url_env = spec.env_keys[1] if len(spec.env_keys) > 1 else ""
    snapshot_path = os.getenv(snapshot_env, "").strip()
    source_url = os.getenv(source_url_env, "").strip() or spec.default_url

    if not snapshot_path:
        raise ProviderFetchError(
            spec.provider_id,
            f"Provider {spec.provider_id} requires {snapshot_env} to point to a local JSON snapshot.",
        )

    path = Path(snapshot_path)
    data = _read_json_array(path, provider_id=spec.provider_id)
    return ProviderFetchResult(
        spec=spec,
        records=data,
        metadata={
            "source_path": str(path),
            "source_url": source_url,
        },
    )


def fetch_provider_records(provider_id: str) -> ProviderFetchResult:
    spec = get_provider_spec(provider_id)
    if spec.mode == "local_seed":
        return _fetch_local_seed(spec)
    if spec.mode == "official_snapshot":
        return _fetch_official_snapshot(spec)
    if spec.mode == "placeholder":
        raise ProviderFetchError(
            provider_id,
            f"Provider {provider_id} is a placeholder. "
            "Prefer the corresponding *_official provider and configure its snapshot path.",
        )
    raise ProviderFetchError(provider_id, f"Unsupported provider mode: {spec.mode}")
