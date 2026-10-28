"""Zero-config automation: persistent invites, auto-join, gateway keys, discovery, auto-connect."""
import json
import time

import httpx
from fastapi.testclient import TestClient

from protocols import (
    PROTOCOL_VERSION,
    EnrollmentRequest,
    HardwareCapabilities,
    NodeRole,
    generate_keypair,
    public_key_to_hex,
    sign_message,
)
from protocols.crypto import canonicalize_json
from racon.agent.worker import RaconAgent
from racon.cluster import discovery
from racon.cluster.auth import ApiKeyStore
from racon.cluster.config import WorkerEndpoint
from racon.coordinator.invites import InviteManager
from racon.coordinator.registry import NodeRegistry
from racon.coordinator.server import create_worker_api
from racon.coordinator.tasks import TaskManager


def test_invites_persist_across_restart(tmp_path):
    store = tmp_path / "invites.json"
    first = InviteManager(store_path=store)
    code = first.create_invite(max_uses=2)

    second = InviteManager(store_path=store)
    assert second.validate_and_consume(code) is True
    assert second.validate_and_consume(code) is True
    assert second.validate_and_consume(code) is False


def _enrollment(code, node_id="node-autotest"):
    priv, pub = generate_keypair()
    pub_hex = public_key_to_hex(pub)
    caps = HardwareCapabilities(
        cpu_name="Test CPU", cpu_cores=4, ram_total_mb=8000.0, ram_available_mb=4000.0,
        runtimes=[], os_name="Windows 11", rpc_endpoint="100.64.0.9:50052",
    )
    now = time.time()
    payload = canonicalize_json({
        "node_id": node_id, "public_key_hex": pub_hex, "invite_code": code,
        "role": NodeRole.CONTRIBUTOR.value, "capabilities": caps.model_dump(),
        "protocol_version": PROTOCOL_VERSION, "timestamp": now,
    })
    return EnrollmentRequest(
        node_id=node_id, public_key_hex=pub_hex, invite_code=code,
        role=NodeRole.CONTRIBUTOR, capabilities=caps,
        protocol_version=PROTOCOL_VERSION, timestamp=now,
        signature=sign_message(priv, payload),
    ), priv, pub_hex


def _coordinator(tmp_path, auto_join="RCN-TEST-JOIN"):
    priv, _pub = generate_keypair()
    registry = NodeRegistry(coordinator_public_key_hex="0" * 64)
    app = create_worker_api(
        registry=registry,
        invites=InviteManager(store_path=tmp_path / "invites.json"),
        tasks=TaskManager(coordinator_private_key=priv),
        auto_approve=True,
        auto_join_code=auto_join,
        gateway_keys_path=tmp_path / "api_keys.json",
    )
    return app, registry


def test_auto_join_enrolls_and_issues_gateway_key(tmp_path):
    app, registry = _coordinator(tmp_path)
    req, _priv, _pub = _enrollment("RCN-TEST-JOIN")
    with TestClient(app) as client:
        resp = client.post("/enroll", json=req.model_dump(mode="json"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["trust_state"] == "approved"
    assert body["gateway_key"].startswith("rk_")

    store = ApiKeyStore(tmp_path / "api_keys.json")
    assert store.verify(body["gateway_key"]) is True
    assert len(registry.list_nodes()) == 1


def test_wrong_join_code_is_rejected(tmp_path):
    app, _ = _coordinator(tmp_path)
    req, _priv, _pub = _enrollment("RCN-WRONG-CODE")
    with TestClient(app) as client:
        resp = client.post("/enroll", json=req.model_dump(mode="json"))
    body = resp.json()
    assert body["status"] == "rejected"
    assert body.get("gateway_key") is None


def test_discover_reports_auto_join(tmp_path):
    app, _ = _coordinator(tmp_path)
    with TestClient(app) as client:
        body = client.get("/discover").json()
    assert body["service"] == "racon-worker-ingress"
    assert body["auto_join"] is True
    assert body["gateway_url"].startswith("https://")


def test_discovery_returns_only_approved_rpc_workers(monkeypatch):
    nodes = [
        {"node_id": "n1", "trust_state": "approved",
         "capabilities": {"rpc_endpoint": "100.64.0.1:50052"}},
        {"node_id": "n2", "trust_state": "pending",
         "capabilities": {"rpc_endpoint": "100.64.0.2:50052"}},
        {"node_id": "n3", "trust_state": "approved",
         "capabilities": {"rpc_endpoint": "127.0.0.1:50052"}},
        {"node_id": "n4", "trust_state": "approved", "capabilities": {}},
    ]

    class Resp:
        status_code = 200

        def json(self):
            return nodes

    monkeypatch.setattr(discovery.httpx, "get", lambda *a, **k: Resp())
    workers = discovery.discover_workers()
    assert workers == [WorkerEndpoint(name="n1", host="100.64.0.1", port=50052)]


def test_discovery_returns_empty_when_admin_down(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(discovery.httpx, "get", boom)
    assert discovery.discover_workers() == []


def test_auto_connect_binds_tailscale_and_starts_rpc(monkeypatch):
    agent = RaconAgent(node_id_override="node-autoconnect")
    started = {}

    monkeypatch.setattr(agent, "detect_bind_ip", staticmethod(lambda: "100.64.0.5"))

    def fake_enroll(code):
        started["code"] = code
        return True

    def fake_start(host="127.0.0.1", port=50052, threads=4):
        started["host"] = host
        started["port"] = port
        agent.rpc_endpoint = f"{host}:{port}"
        return True

    monkeypatch.setattr(agent, "enroll", fake_enroll)
    monkeypatch.setattr(agent, "start_rpc_worker", fake_start)

    ok, message = agent.auto_connect(join_code="RCN-JOIN")
    assert ok is True
    assert started["code"] == "RCN-JOIN"
    assert started["host"] == "100.64.0.5"
    assert agent.status.coordinator_url.startswith("https://")
    assert "RPC listening on 100.64.0.5" in message


def test_auto_connect_never_binds_all_interfaces(monkeypatch):
    agent = RaconAgent(node_id_override="node-wildcard")
    seen = {}

    monkeypatch.setattr(agent, "enroll", lambda code: True)

    def fake_start(host="127.0.0.1", port=50052, threads=4):
        seen["host"] = host
        agent.rpc_endpoint = f"{host}:{port}"
        return True

    monkeypatch.setattr(agent, "start_rpc_worker", fake_start)
    ok, _ = agent.auto_connect(rpc_bind="0.0.0.0")
    assert ok is True
    assert seen["host"] == "127.0.0.1"
