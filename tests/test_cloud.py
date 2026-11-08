"""Read-only Supabase cloud catalog tests (no network)."""
from __future__ import annotations

import asyncio

import pytest

from racon.cloud.supabase import SupabaseCatalogClient, SupabaseConfig, SupabaseError


class _Response:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.headers = headers or {}
        self.content = b"x" if payload is not None else b""

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, routes):
        self._routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        for fragment, response in self._routes.items():
            if fragment in url:
                return response
        return _Response(404, {"message": "not found"})


def _client(routes):
    config = SupabaseConfig(url="https://example.supabase.co", key="sb_secret_value_123", key_kind="secret")
    return SupabaseCatalogClient(
        config=config,
        client_factory=lambda: _FakeClient(routes),
    )


def test_status_reports_counts_and_never_returns_the_key():
    routes = {
        "/rest/v1/models": _Response(206, [], {"content-range": "0-0/4"}),
        "/rest/v1/model_versions": _Response(200, [], {"content-range": "0-0/0"}),
        "/rest/v1/nodes": _Response(200, [], {"content-range": "0-0/1"}),
        "/rest/v1/scheduler_decisions": _Response(200, [], {"content-range": "0-0/0"}),
    }
    status = asyncio.run(_client(routes).status())
    assert status["configured"] is True
    assert status["reachable"] is True
    assert status["tables"]["models"]["count"] == "4"
    assert status["host"] == "example.supabase.co"
    assert "sb_secret_value_123" not in repr(status)


def test_unconfigured_client_is_a_clear_noop():
    client = SupabaseCatalogClient(config=SupabaseConfig(url="", key="", key_kind=""))
    status = asyncio.run(client.status())
    assert status["configured"] is False
    assert asyncio.run(client.list_models()) == []


def test_list_models_maps_metadata_and_marks_not_installable():
    payload = [
        {
            "id": "m1",
            "name": "Gemma 3 1B",
            "family": "gemma",
            "description": "small",
            "context_length": 8192,
            "parameter_count": 1_000_000_000,
            "quantization": "q4_K_M",
        }
    ]
    routes = {"/rest/v1/models": _Response(200, payload)}
    models = asyncio.run(_client(routes).list_models())
    assert models == [
        {
            "id": "m1",
            "name": "Gemma 3 1B",
            "family": "gemma",
            "description": "small",
            "context_length": 8192,
            "parameter_count": 1_000_000_000,
            "quantization": "q4_K_M",
            "provider_label": None,
            "installable": False,
        }
    ]


def test_supabase_error_on_bad_gateway():
    routes = {"/rest/v1/models": _Response(502, {"message": "bad"})}
    with pytest.raises(SupabaseError):
        asyncio.run(_client(routes).list_models())


def test_cloud_api_status_and_models(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.racon.api import routes_cloud
    from backend.racon.api.app import app

    class Stub:
        configured = True

        async def status(self):
            return {
                "configured": True,
                "reachable": True,
                "host": "example.supabase.co",
                "key_kind": "secret",
                "tables": {"models": {"status": 200, "count": "4"}},
                "error": None,
            }

        async def list_models(self, limit=50):
            return [{"id": "m1", "name": "Model", "installable": False}]

        async def list_endpoints(self, ids):
            return {"m1": [{"id": "e1"}]}

    monkeypatch.setattr(routes_cloud, "get_supabase_client", lambda: Stub())
    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        status = client.get("/api/v1/cloud/status")
        assert status.status_code == 200
        assert status.json()["tables"]["models"]["count"] == "4"
        assert "sb_secret_value_123" not in status.text

        models = client.get("/api/v1/cloud/models")
        assert models.status_code == 200
        assert models.json()["models"][0]["id"] == "m1"
        assert models.json()["installable_count"] == 0
