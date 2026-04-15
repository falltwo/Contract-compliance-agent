"""Admin service: system status, ollama list, docker ps, and service restart."""

from __future__ import annotations

import re
import subprocess

from backend.schemas.admin import (
    DockerContainerInfo,
    DockerContainersResponse,
    OllamaModelInfo,
    OllamaModelsResponse,
    ServiceStatus,
)

ALLOWED_SERVICES = (
    "contract-agent-api.service",
    "contract-agent-web.service",
    "ollama.service",
)

DEFAULT_RESTART_SERVICES = (
    "contract-agent-api.service",
    "contract-agent-web.service",
)


def _run_cmd(args: list[str], timeout: int = 12) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def _clean_err(stderr: str, stdout: str) -> str:
    msg = (stderr or "").strip() or (stdout or "").strip()
    return msg[:500] if msg else "unknown error"


def _parse_service_show(output: str) -> tuple[str, str, str, str | None]:
    lines = [ln.strip() for ln in output.splitlines()]
    while len(lines) < 4:
        lines.append("")
    active_state, sub_state, unit_file_state, description = lines[:4]
    return (
        active_state or "unknown",
        sub_state or "unknown",
        unit_file_state or "unknown",
        description or None,
    )


def get_service_status(service_name: str) -> ServiceStatus:
    if service_name not in ALLOWED_SERVICES:
        return ServiceStatus(name=service_name, error="service not allowed")

    proc = _run_cmd(
        [
            "sudo",
            "-n",
            "systemctl",
            "show",
            service_name,
            "--property=ActiveState",
            "--property=SubState",
            "--property=UnitFileState",
            "--property=Description",
            "--value",
        ],
        timeout=12,
    )
    if proc.returncode != 0:
        return ServiceStatus(name=service_name, error=_clean_err(proc.stderr, proc.stdout))

    active_state, sub_state, unit_file_state, description = _parse_service_show(proc.stdout)
    return ServiceStatus(
        name=service_name,
        description=description,
        active_state=active_state,
        sub_state=sub_state,
        unit_file_state=unit_file_state,
    )


def list_services_status() -> list[ServiceStatus]:
    return [get_service_status(name) for name in ALLOWED_SERVICES]


def restart_services(services: list[str]) -> tuple[list[str], list[str], list[ServiceStatus]]:
    restarted: list[str] = []
    failed: list[str] = []
    statuses: list[ServiceStatus] = []

    for name in services:
        proc = _run_cmd(["sudo", "-n", "systemctl", "restart", name], timeout=20)
        if proc.returncode == 0:
            restarted.append(name)
        else:
            failed.append(name)
        statuses.append(get_service_status(name))

    return restarted, failed, statuses


def list_ollama_models() -> OllamaModelsResponse:
    proc = _run_cmd(["ollama", "list"], timeout=20)
    if proc.returncode != 0:
        return OllamaModelsResponse(error=_clean_err(proc.stderr, proc.stdout))

    lines = [ln.rstrip() for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return OllamaModelsResponse(models=[])

    body = lines[1:] if lines[0].strip().upper().startswith("NAME") else lines
    models: list[OllamaModelInfo] = []
    for row in body:
        cols = re.split(r"\s{2,}", row.strip())
        if len(cols) < 4:
            continue
        models.append(
            OllamaModelInfo(
                name=cols[0],
                model_id=cols[1],
                size=cols[2],
                modified="  ".join(cols[3:]).strip() or cols[3],
            )
        )
    return OllamaModelsResponse(models=models)


def list_docker_containers() -> DockerContainersResponse:
    proc = _run_cmd(
        [
            "sudo",
            "-n",
            "docker",
            "ps",
            "--format",
            "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.State}}",
        ],
        timeout=20,
    )
    if proc.returncode != 0:
        return DockerContainersResponse(
            engine_available=False,
            error=_clean_err(proc.stderr, proc.stdout),
        )

    rows = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    containers: list[DockerContainerInfo] = []
    for row in rows:
        cols = row.split("\t")
        if len(cols) < 5:
            continue
        containers.append(
            DockerContainerInfo(
                container_id=cols[0],
                name=cols[1],
                image=cols[2],
                status=cols[3],
                state=cols[4],
            )
        )

    return DockerContainersResponse(engine_available=True, containers=containers)

