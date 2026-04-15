"""Admin API schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServiceStatus(BaseModel):
    name: str
    description: str | None = None
    active_state: str = "unknown"
    sub_state: str = "unknown"
    unit_file_state: str = "unknown"
    error: str | None = None


class ServicesStatusResponse(BaseModel):
    services: list[ServiceStatus] = Field(default_factory=list)


class ServicesRestartRequest(BaseModel):
    services: list[str] = Field(
        default_factory=list,
        description="Service names to restart. Empty means default restart set.",
    )


class ServicesRestartResponse(BaseModel):
    requested_services: list[str] = Field(default_factory=list)
    restarted_services: list[str] = Field(default_factory=list)
    failed_services: list[str] = Field(default_factory=list)
    services: list[ServiceStatus] = Field(default_factory=list)


class OllamaModelInfo(BaseModel):
    name: str
    model_id: str
    size: str
    modified: str


class OllamaModelsResponse(BaseModel):
    models: list[OllamaModelInfo] = Field(default_factory=list)
    error: str | None = None


class DockerContainerInfo(BaseModel):
    container_id: str
    name: str
    image: str
    status: str
    state: str


class DockerContainersResponse(BaseModel):
    engine_available: bool
    containers: list[DockerContainerInfo] = Field(default_factory=list)
    error: str | None = None
