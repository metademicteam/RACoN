"""Hardware-based model recommendation and local telemetry persistence."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.racon.api.app import app


def _client() -> TestClient:
    return TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"})


def test_recommendations_are_derived_from_measured_hardware():
    with _client() as client:
        response = client.get("/api/v1/models/recommendations")
        assert response.status_code == 200
        data = response.json()

    measured = data["measured"]
    assert measured["ram_total_mb"] is not None and measured["ram_total_mb"] > 0
    assert measured["ram_available_mb"] is not None
    assert isinstance(measured["gpu_telemetry_available"], bool)

    recommendations = data["recommendations"]
    assert recommendations
    for entry in recommendations:
        assert entry["fits"] in (True, False, None)
        assert entry["reason"]
        assert entry["source"] in ("installed", "catalog", "huggingface")
        if entry["fits"] is True:
            assert entry["min_ram_mb"] is None or entry["min_ram_mb"] <= measured["ram_available_mb"]

    if data["recommended_id"]:
        chosen = next(item for item in recommendations if item["id"] == data["recommended_id"])
        assert chosen["fits"] is True


def test_installed_models_use_gguf_estimate_when_present():
    with _client() as client:
        data = client.get("/api/v1/models/recommendations").json()

    installed = [item for item in data["recommendations"] if item["installed"]]
    for entry in installed:
        # Either the header produced a real requirement, or the reason says it could not.
        if entry["min_ram_mb"] is None:
            assert "could not be derived" in entry["reason"]
        else:
            assert entry["min_ram_mb"] > 0


def test_node_heartbeat_updates_measured_ram(isolated_db):
    isolated_db.upsert_node(
        {
            "id": "node-heartbeat",
            "name": "Heartbeat Node",
            "is_local": True,
            "status": "available",
            "ram_total_mb": 16384.0,
            "ram_available_mb": 8192.0,
            "capabilities": {"cpu_name": "Test CPU", "cpu_logical_count": 8},
        }
    )
    assert isolated_db.update_node_heartbeat("node-heartbeat", ram_available=6144.0) is True

    node = isolated_db.get_node("node-heartbeat")
    assert node["ram_available_mb"] == 6144.0
    assert node["last_heartbeat"] is not None

    assert isolated_db.update_node_heartbeat("missing-node", ram_available=1.0) is False
