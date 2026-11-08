"""Tests for RPC link handling: self-endpoint discovery, cache flag, and measured rate caps."""
import time

import httpx
import pytest

from racon.cluster import discovery
from racon.cluster.config import ClusterConfig, WorkerEndpoint, load_config
from racon.cluster.head import build_head_command
from racon.cluster.planner import plan_layers
from racon.cluster.throughput import layers_that_fit, load_rates, record_load, slowest_rate
from racon.runtime.rpc_worker import RpcServerProcess


def _nodes(*entries):
    return [
        {
            "node_id": name,
            "trust_state": "approved",
            "capabilities": {"rpc_endpoint": endpoint},
        }
        for name, endpoint in entries
    ]


def test_discovery_ignores_the_heads_own_endpoint(monkeypatch):
    payload = _nodes(
        ("self", "100.76.208.54:50052"),
        ("node-ac57a79d14c4", "100.125.77.125:50052"),
    )
    monkeypatch.setattr(
        discovery.httpx, "get", lambda *a, **k: httpx.Response(200, json=payload)
    )
    workers = discovery.discover_workers(local_hosts={"100.76.208.54"})
    assert [w.address for w in workers] == ["100.125.77.125:50052"]


def test_discovery_ignores_loopback_endpoints(monkeypatch):
    payload = _nodes(("self", "127.0.0.1:50052"))
    monkeypatch.setattr(
        discovery.httpx, "get", lambda *a, **k: httpx.Response(200, json=payload)
    )
    assert discovery.discover_workers(local_hosts={"100.76.208.54"}) == []


def test_discovery_skips_unapproved_nodes(monkeypatch):
    payload = [{
        "node_id": "pending",
        "trust_state": "pending",
        "capabilities": {"rpc_endpoint": "100.125.77.125:50052"},
    }]
    monkeypatch.setattr(
        discovery.httpx, "get", lambda *a, **k: httpx.Response(200, json=payload)
    )
    assert discovery.discover_workers(local_hosts={"100.76.208.54"}) == []


def test_rpc_server_uses_memory_only_by_default():
    rpc = RpcServerProcess(host="100.125.77.125", port=50052, threads=4)
    cmd = rpc.build_command()
    assert "-c" not in cmd
    assert cmd[cmd.index("-H") + 1] == "100.125.77.125"


def test_rpc_server_cache_can_be_enabled(monkeypatch):
    monkeypatch.setenv("RACON_RPC_CACHE", "1")
    assert "-c" in RpcServerProcess(host="127.0.0.1", port=50052).build_command()

    monkeypatch.delenv("RACON_RPC_CACHE")
    assert "-c" in RpcServerProcess(
        host="127.0.0.1", port=50052, enable_cache=True
    ).build_command()


def test_layers_that_fit_reserves_head_startup_room():
    # 0.5 MiB/s for 120 s with 0.8 headroom is 48 MiB, i.e. 2 layers of 20 MiB.
    assert layers_that_fit(0.5, 20.0, 120.0) == 2
    assert layers_that_fit(0.0, 20.0, 120.0) == 0


def test_record_load_keeps_an_exponential_average(tmp_path):
    path = tmp_path / "rates.json"
    record_load(["100.1.1.1:50052"], worker_mb=100.0, seconds=10.0, path=path)
    assert load_rates(path)["100.1.1.1:50052"]["mb_per_s"] == pytest.approx(10.0)

    record_load(["100.1.1.1:50052"], worker_mb=100.0, seconds=50.0, path=path)
    # EMA towards the new 2 MiB/s sample instead of replacing the link estimate outright.
    assert load_rates(path)["100.1.1.1:50052"]["mb_per_s"] == pytest.approx(6.8, abs=0.05)
    assert slowest_rate(["100.1.1.1:50052"], load_rates(path)) == pytest.approx(6.8, abs=0.05)


def test_conservative_multi_worker_cap_uses_the_slowest_link():
    now = time.time()
    rates = {
        "a": {"mb_per_s": 10.0, "updated": now},
        "b": {"mb_per_s": 2.0, "updated": now},
    }
    assert slowest_rate(["a", "b"], rates) == 2.0
    assert slowest_rate(["a"], rates) == 10.0
    assert slowest_rate(["c"], rates) is None


def test_load_config_validates_the_split_policy(tmp_path):
    path = tmp_path / "cluster.json"
    path.write_text('{"model_path": "m.gguf", "worker_layer_policy": "sometimes"}', encoding="utf-8")
    with pytest.raises(ValueError, match="worker_layer_policy"):
        load_config(path)


def test_load_config_reads_the_layer_cap(tmp_path):
    path = tmp_path / "cluster.json"
    path.write_text(
        '{"model_path": "m.gguf", "max_worker_layers": 4, "worker_layer_policy": "always",'
        ' "rpc_stall_timeout_s": 90, "rpc_startup_timeout_s": 240}',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.max_worker_layers == 4
    assert cfg.worker_layer_policy == "always"
    assert cfg.rpc_stall_timeout_s == 90.0
    assert cfg.rpc_startup_timeout_s == 240.0


def test_head_command_follows_plan_order_not_caller_order():
    cfg = ClusterConfig(model_path="m.gguf")
    a = WorkerEndpoint("a", "100.1.1.1", 50052)
    b = WorkerEndpoint("b", "100.1.1.2", 50052)
    plan = plan_layers(
        26, head_free_mb=2000, worker_free_mb={"a": 3000, "b": 3000},
        bytes_per_layer_mb=20.0, worker_order=["b", "a"],
    )
    cmd = build_head_command(cfg, "llama-server", "k", plan=plan, active_workers=[a, b])
    assert cmd[cmd.index("--rpc") + 1] == "100.1.1.2:50052,100.1.1.1:50052"
    assert cmd[cmd.index("-dev") + 1] == "RPC0,RPC1"
