"""Administrator and limited-scope client token separation."""
import pytest
from fastapi.testclient import TestClient

from backend.racon.api.app import app
from backend.racon.core.config import get_settings

ADMIN_TOKEN = "racon-local-dev-token"
CLIENT_TOKEN = "test-client-token"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client_token():
    settings = get_settings()
    original = settings.CLIENT_TOKEN
    settings.CLIENT_TOKEN = CLIENT_TOKEN
    yield CLIENT_TOKEN
    settings.CLIENT_TOKEN = original


def test_client_token_reaches_normal_api(client, client_token):
    r = client.get("/api/v1/status", headers={"X-RACON-TOKEN": client_token})
    assert r.status_code == 200


def test_client_token_cannot_reach_admin_api(client, client_token):
    r = client.get("/api/v1/admin/overview", headers={"X-RACON-TOKEN": client_token})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_admin_token_reaches_admin_api(client, client_token):
    r = client.get("/api/v1/admin/overview", headers={"X-RACON-TOKEN": ADMIN_TOKEN})
    assert r.status_code == 200


def test_client_token_can_read_settings(client, client_token):
    r = client.get("/api/v1/settings", headers={"X-RACON-TOKEN": client_token})
    assert r.status_code == 200


def test_client_token_cannot_change_settings(client, client_token):
    r = client.put(
        "/api/v1/settings",
        json={"key": "log_level", "value": "DEBUG"},
        headers={"X-RACON-TOKEN": client_token},
    )
    assert r.status_code == 403


def test_unknown_token_is_rejected(client, client_token):
    r = client.get("/api/v1/status", headers={"X-RACON-TOKEN": "not-a-token"})
    assert r.status_code == 401


def test_empty_client_token_disables_client_access(client):
    settings = get_settings()
    original = settings.CLIENT_TOKEN
    settings.CLIENT_TOKEN = ""
    try:
        r = client.get("/api/v1/status", headers={"X-RACON-TOKEN": CLIENT_TOKEN})
        assert r.status_code == 401
    finally:
        settings.CLIENT_TOKEN = original
