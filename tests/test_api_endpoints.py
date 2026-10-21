"""Comprehensive test suite for RACoN FastAPI REST endpoints."""
import pytest
from fastapi.testclient import TestClient

from backend.racon.api.app import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as c:
        yield c


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_status_endpoint(client):
    r = client.get("/api/v1/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "online"
    assert "hardware" in data
    assert data["hardware"]["cpu_name"] != ""
    assert data["hardware"]["ram_total_mb"] > 0


def test_nodes_endpoint(client):
    r = client.get("/api/v1/nodes")
    assert r.status_code == 200
    nodes = r.json()
    assert isinstance(nodes, list)
    assert len(nodes) >= 1
    # Check local node exists
    local = next((n for n in nodes if n["is_local"]), None)
    assert local is not None
    assert local["cpu_cores"] > 0


def test_models_endpoint(client):
    r = client.get("/api/v1/models")
    assert r.status_code == 200
    models = r.json()
    assert isinstance(models, list)


def test_model_catalog_endpoint(client):
    r = client.get("/api/v1/models/catalog")
    assert r.status_code == 200
    data = r.json()
    assert "catalog" in data
    assert "installed_count" in data
    assert "total_available" in data
    assert len(data["catalog"]) >= 5
    # Verify Handy-style fields are populated
    first = data["catalog"][0]
    assert "accuracy_score" in first
    assert "speed_score" in first
    assert "size_display" in first
    assert "tags" in first


def test_model_pull_and_status_endpoint(client):
    r = client.post("/api/v1/models/pull", json={"model_name": "qwen2.5:0.5b"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "initiated"

    r_status = client.get("/api/v1/models/pull/status?model_name=qwen2.5:0.5b")
    assert r_status.status_code == 200
    status_data = r_status.json()
    assert "status" in status_data
    assert "percentage" in status_data


def test_scheduler_preview_endpoint(client):
    r = client.post(
        "/api/v1/scheduler/preview",
        json={
            "scenario_name": "Multiple Feasible Candidates",
            "objective": "balanced",
            "mode": "deterministic",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["final_choice"] is not None
    assert len(data["evaluations"]) == 3


def test_scheduler_preview_infeasible(client):
    r = client.post(
        "/api/v1/scheduler/preview",
        json={
            "scenario_name": "No Feasible Solution",
            "objective": "balanced",
            "mode": "deterministic",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is False
    assert data["final_choice"] is None
    assert data["failure_reason"] is not None


def test_jobs_endpoint(client):
    r = client.get("/api/v1/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_benchmarks_endpoint(client):
    r = client.get("/api/v1/benchmarks")
    assert r.status_code == 200
    benches = r.json()
    assert isinstance(benches, list)
    # The preview scheduler test above recorded a benchmark
    assert len(benches) >= 1


def test_settings_get_and_put(client):
    r = client.put("/api/v1/settings", json={"key": "test_param", "value": "test_val"})
    assert r.status_code == 200

    r = client.get("/api/v1/settings")
    assert r.status_code == 200
    data = r.json()
    assert data.get("test_param") == "test_val"


def test_logs_endpoint(client):
    r = client.get("/api/v1/logs?limit=10")
    assert r.status_code == 200
    logs = r.json()
    assert isinstance(logs, list)
    assert len(logs) >= 1


def test_openai_compatibility_models(client):
    """Verify OpenAI-compatible /v1/models endpoint."""
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"]) >= 1
    model_ids = [m["id"] for m in data["data"]]
    assert "gemma-3-1b" in model_ids


def test_openai_compatibility_chat_completions(client):
    """Verify OpenAI-compatible /v1/chat/completions endpoint."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "gemma-3-1b",
            "messages": [
                {"role": "user", "content": "Hello RACoN!"}
            ],
            "max_tokens": 16,
            "stream": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert len(data["choices"][0]["message"]["content"]) > 0
    assert "usage" in data
