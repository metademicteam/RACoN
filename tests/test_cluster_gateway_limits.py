"""Rate limits and request caps on the public gateway."""
import json

from fastapi.testclient import TestClient

from racon.cluster.auth import ApiKeyStore
from racon.cluster.config import ClusterConfig
from racon.cluster.gateway import create_gateway
from racon.cluster.ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_limiter_allows_up_to_max_then_blocks():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(2, window_s=60, clock=clock)
    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is True
    allowed, retry = limiter.check("a")
    assert allowed is False
    assert 0 < retry <= 60


def test_limiter_window_slides():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, window_s=10, clock=clock)
    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is False
    clock.now = 10.5
    assert limiter.check("a")[0] is True


def test_limiter_keys_are_independent():
    limiter = SlidingWindowLimiter(1, window_s=60, clock=FakeClock())
    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True


def _app(tmp_path, **kwargs):
    keys = ApiKeyStore(tmp_path / "keys.json")
    token = keys.create("test")
    cfg = ClusterConfig(model_path="m.gguf", max_tokens_limit=100)
    app = create_gateway(cfg, keys, "http://head.invalid", "internal", **kwargs)
    return app, token


def test_gateway_returns_429_after_ip_limit(tmp_path):
    app, token = _app(tmp_path, ip_limit_per_min=2, key_limit_per_min=100)
    headers = {"Authorization": f"Bearer {token}", "CF-Connecting-IP": "203.0.113.9"}
    with TestClient(app) as c:
        c.get("/v1/models", headers=headers)
        c.get("/v1/models", headers=headers)
        r = c.get("/v1/models", headers=headers)
        assert r.status_code == 429
        assert "Retry-After" in r.headers


def test_gateway_ip_limit_applies_before_auth(tmp_path):
    app, _ = _app(tmp_path, ip_limit_per_min=1, key_limit_per_min=100)
    with TestClient(app) as c:
        assert c.get("/v1/models", headers={"CF-Connecting-IP": "198.51.100.1"}).status_code == 401
        assert c.get("/v1/models", headers={"CF-Connecting-IP": "198.51.100.1"}).status_code == 429


def test_gateway_returns_429_after_key_limit(tmp_path):
    app, token = _app(tmp_path, ip_limit_per_min=100, key_limit_per_min=1)
    with TestClient(app) as c:
        headers = {"Authorization": f"Bearer {token}"}
        c.get("/v1/models", headers=headers)
        assert c.get("/v1/models", headers=headers).status_code == 429


def test_gateway_rejects_max_tokens_above_limit(tmp_path):
    app, token = _app(tmp_path)
    body = json.dumps({"model": "m", "messages": [], "max_tokens": 101}).encode()
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", content=body,
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        assert r.status_code == 400
        assert "max_tokens" in r.json()["error"]["message"]


def test_gateway_passes_max_tokens_within_limit_to_head(tmp_path):
    app, token = _app(tmp_path)
    body = json.dumps({"model": "m", "messages": [], "max_tokens": 100}).encode()
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", content=body,
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        assert r.status_code == 503  # passed the cap; head is not running in tests
