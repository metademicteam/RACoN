"""Enrollment URL normalization and the worker API landing route."""
import httpx
from fastapi.testclient import TestClient

from racon.agent.worker import RaconAgent
from racon.coordinator.invites import InviteManager
from racon.coordinator.registry import NodeRegistry
from racon.coordinator.server import create_worker_api
from racon.coordinator.tasks import TaskManager
from protocols.crypto import generate_keypair


def _agent():
    return RaconAgent(node_id_override="test-enroll-url")


def test_enroll_strips_trailing_slash_and_enroll_suffix(monkeypatch):
    agent = _agent()
    seen = {}

    def fake_post(self, url, **kwargs):
        seen["url"] = url
        raise httpx.ConnectError("stop here")

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    for entered in (
        "https://example.test/",
        "https://example.test/enroll",
        "https://example.test/enroll/",
        "  https://example.test  ",
    ):
        agent.coordinator_url = entered
        agent.enroll("RCN-ABCDEF123456")
        assert seen["url"] == "https://example.test/enroll"


def test_worker_api_root_explains_service():
    priv, pub = generate_keypair()
    registry = NodeRegistry(coordinator_public_key_hex=pub.hex() if isinstance(pub, bytes) else pub)
    app = create_worker_api(
        registry=registry,
        invites=InviteManager(),
        tasks=TaskManager(coordinator_private_key=priv),
        auto_approve=True,
    )
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "racon-worker-ingress"
        assert "Worker Agent" in body["message"]
