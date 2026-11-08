"""RACoN API-key issuance, scoping and rate limiting."""
import pytest
from fastapi.testclient import TestClient

from backend.racon.api.app import app
from backend.racon.network.api_keys import get_api_key_service, reset_api_key_service
from backend.racon.storage.db import session_scope
from backend.racon.storage.models import ApiKey

ADMIN_HEADERS = {"X-RACON-TOKEN": "racon-local-dev-token"}


def _clear_keys():
    with session_scope() as session:
        session.query(ApiKey).delete()
    reset_api_key_service()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_keys():
    _clear_keys()
    yield
    _clear_keys()


def _create(client, **overrides):
    payload = {"name": "pytest", "rate_limit_per_min": 60}
    payload.update(overrides)
    response = client.post("/api/v1/admin/keys", json=payload, headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_create_list_and_hash_storage(client):
    created = _create(client)
    assert created["key"].startswith("rk_")
    assert created["prefix"] == created["key"][:12]

    listing = client.get("/api/v1/admin/keys", headers=ADMIN_HEADERS)
    assert listing.status_code == 200
    data = listing.json()["data"]
    assert len(data) == 1
    assert "key" not in data[0]
    assert data[0]["prefix"] == created["prefix"]

    with session_scope() as session:
        stored = session.query(ApiKey).one()
        assert stored.key_hash != created["key"]
        assert len(stored.key_hash) == 64


def test_key_allows_v1_and_is_rejected_elsewhere(client):
    created = _create(client)
    token = created["key"]

    models = client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert models.status_code == 200
    assert any(item["id"] == "racon" for item in models.json()["data"])

    blocked = client.get("/api/v1/status", headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "insufficient_scope"


def test_x_api_key_header_is_accepted(client):
    token = _create(client)["key"]
    response = client.get("/v1/models", headers={"x-api-key": token})
    assert response.status_code == 200


def test_invalid_key_gets_openai_error(client):
    response = client.get("/v1/models", headers={"Authorization": "Bearer rk_not-a-real-key"})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "invalid_api_key"
    assert body["error"]["type"] == "authentication_error"


def test_revoked_key_is_rejected(client):
    created = _create(client)
    revoked = client.delete(f"/api/v1/admin/keys/{created['id']}", headers=ADMIN_HEADERS)
    assert revoked.status_code == 200

    response = client.get("/v1/models", headers={"Authorization": f"Bearer {created['key']}"})
    assert response.status_code == 401


def test_rate_limit_returns_429(client):
    created = _create(client, rate_limit_per_min=2)
    headers = {"Authorization": f"Bearer {created['key']}"}

    assert client.get("/v1/models", headers=headers).status_code == 200
    assert client.get("/v1/models", headers=headers).status_code == 200
    limited = client.get("/v1/models", headers=headers)

    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
