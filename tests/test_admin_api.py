"""Admin API tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.schemas.admin import ServiceStatus


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@patch("backend.services.admin_service.list_services_status")
def test_get_admin_services(mock_list: object, client: TestClient) -> None:
    mock_list.return_value = [
        ServiceStatus(
            name="contract-agent-api.service",
            active_state="active",
            sub_state="running",
            unit_file_state="enabled",
        )
    ]
    r = client.get("/api/v1/admin/services")
    assert r.status_code == 200
    body = r.json()
    assert body["services"][0]["name"] == "contract-agent-api.service"
    assert body["services"][0]["active_state"] == "active"


@patch("backend.services.admin_service.restart_services")
def test_restart_admin_services_default(mock_restart: object, client: TestClient) -> None:
    mock_restart.return_value = (
        ["contract-agent-api.service"],
        [],
        [
            ServiceStatus(
                name="contract-agent-api.service",
                active_state="active",
                sub_state="running",
                unit_file_state="enabled",
            )
        ],
    )
    r = client.post("/api/v1/admin/services/restart", json={"services": []})
    assert r.status_code == 200
    body = r.json()
    assert "contract-agent-api.service" in body["requested_services"]
    assert body["failed_services"] == []


def test_restart_admin_services_rejects_not_allowed(client: TestClient) -> None:
    r = client.post("/api/v1/admin/services/restart", json={"services": ["nginx.service"]})
    assert r.status_code == 400


@patch("backend.services.admin_service.list_ollama_models")
def test_get_admin_ollama_models(mock_models: object, client: TestClient) -> None:
    mock_models.return_value = {
        "models": [
            {
                "name": "gemma3:27b",
                "model_id": "abc123",
                "size": "17 GB",
                "modified": "1 minute ago",
            }
        ],
        "error": None,
    }
    r = client.get("/api/v1/admin/ollama/models")
    assert r.status_code == 200
    assert r.json()["models"][0]["name"] == "gemma3:27b"


@patch("backend.services.admin_service.list_docker_containers")
def test_get_admin_docker_containers(mock_docker: object, client: TestClient) -> None:
    mock_docker.return_value = {
        "engine_available": True,
        "containers": [
            {
                "container_id": "123",
                "name": "api",
                "image": "python:3.13",
                "status": "Up 10 minutes",
                "state": "running",
            }
        ],
        "error": None,
    }
    r = client.get("/api/v1/admin/docker/containers")
    assert r.status_code == 200
    assert r.json()["engine_available"] is True
    assert r.json()["containers"][0]["name"] == "api"

