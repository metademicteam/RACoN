"""Tests for the multi-PC cluster: key auth, config validation, gateway routing and head command."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from racon.cluster.auth import ApiKeyStore
from racon.cluster.config import ClusterConfig, WorkerEndpoint, load_config
from racon.cluster.gateway import create_gateway
from racon.cluster.head import build_head_command


def test_api_key_store_stores_only_digest(tmp_path):
    store = ApiKeyStore(tmp_path / "keys.json")
    token = store.create("agent")

    assert token.startswith("rk_")
    assert store.verify(token) is True
    assert token not in (tmp_path / "keys.json").read_text(encoding="utf-8")
    assert "sha256" not in store.list()[0]


def test_api_key_revocation(tmp_path):
    store = ApiKeyStore(tmp_path / "keys.json")
    token = store.create("agent")
    key_id = store.list()[0]["id"]

    assert store.revoke(key_id) is True
    assert store.verify(token) is False
    assert store.revoke(key_id) is False


def test_api_key_rejects_malformed_tokens(tmp_path):
    store = ApiKeyStore(tmp_path / "keys.json")
    store.create("agent")
    assert store.verify("") is False
    assert store.verify("not-a-key") is False
    assert store.verify("rk_wrong") is False


def test_load_config_rejects_wildcard_worker_host(tmp_path):
    path = tmp_path / "cluster.json"
    path.write_text(json.dumps({
        "model_path": "m.gguf",
        "workers": [{"name": "w", "host": "0.0.0.0", "port": 50052}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="concrete"):
        load_config(path)


def test_load_config_parses_workers(tmp_path):
    path = tmp_path / "cluster.json"
    path.write_text(json.dumps({
        "model_path": "m.gguf",
        "workers": [{"name": "pc-b", "host": "100.94.1.116", "port": 50052}],
    }), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.workers == [WorkerEndpoint(name="pc-b", host="100.94.1.116", port=50052)]


def test_head_command_includes_rpc_and_hides_nothing_public():
    cfg = ClusterConfig(
        model_path="m.gguf",
        workers=[WorkerEndpoint("a", "100.1.1.1", 50052), WorkerEndpoint("b", "100.1.1.2", 50052)],
    )
    cmd = build_head_command(cfg, "llama-server", "secret")
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--rpc") + 1] == "100.1.1.1:50052,100.1.1.2:50052"
    assert "--no-webui" in cmd


def test_head_command_without_workers_skips_rpc():
    cmd = build_head_command(ClusterConfig(model_path="m.gguf"), "llama-server", "secret")
    assert "--rpc" not in cmd


@pytest.fixture
def key_store(tmp_path):
    return ApiKeyStore(tmp_path / "keys.json")


def _gateway(store, head_url="http://head.invalid", **kwargs):
    return create_gateway(ClusterConfig(model_path="m.gguf"), store, head_url, "internal", **kwargs)


def test_gateway_rejects_missing_key(key_store):
    key_store.create("test")
    with TestClient(_gateway(key_store)) as c:
        assert c.get("/v1/models").status_code == 401


def test_gateway_rejects_wrong_key(key_store):
    key_store.create("test")
    with TestClient(_gateway(key_store)) as c:
        assert c.get("/v1/models", headers={"Authorization": "Bearer rk_nope"}).status_code == 401


def test_gateway_hides_non_v1_routes(key_store):
    token = key_store.create("test")
    with TestClient(_gateway(key_store)) as c:
        headers = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/v1/status", headers=headers).status_code == 404
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/v1/admin", headers=headers).status_code == 404


def test_gateway_health_is_public(key_store):
    with TestClient(_gateway(key_store)) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_gateway_rejects_oversized_body(key_store):
    token = key_store.create("test")
    with TestClient(_gateway(key_store, max_request_bytes=10)) as c:
        r = c.post("/v1/chat/completions", content=b"x" * 100,
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        assert r.status_code == 413


def test_gateway_returns_503_when_head_down(key_store):
    token = key_store.create("test")
    app = _gateway(key_store, head_url="http://127.0.0.1:1")
    with TestClient(app) as c:
        r = c.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 503
