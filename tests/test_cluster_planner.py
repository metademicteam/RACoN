"""Tests for layer placement, device probing, and supervisor replan behaviour."""
import pytest

from racon.cluster.config import ClusterConfig, WorkerEndpoint
from racon.cluster.head import build_head_command
from racon.cluster.planner import HEAD, plan_layers
from racon.cluster.probe import parse_list_devices
from racon.cluster.supervisor import ClusterSupervisor
from racon.cluster.probe import ProbeResult

LIST_DEVICES_OUTPUT = """Available devices:
  Vulkan0: Intel(R) UHD Graphics 630 (8125 MiB, 7462 MiB free)
  RPC0: 127.0.0.1:50081 (8125 MiB, 7454 MiB free)
  RPC1: 127.0.0.1:50082 (8125 MiB, 7454 MiB free)
"""


def test_parse_list_devices_reads_head_and_rpc_free_memory():
    result = parse_list_devices(LIST_DEVICES_OUTPUT)
    assert result.local_free_mb == 7462.0
    assert result.rpc_free_mb == {"127.0.0.1:50081": 7454.0, "127.0.0.1:50082": 7454.0}


def test_parse_list_devices_handles_unreachable_worker():
    result = parse_list_devices("  Vulkan0: GPU (8000 MiB, 4000 MiB free)\n")
    assert result.local_free_mb == 4000.0
    assert result.rpc_free_mb == {}


def test_plan_covers_every_layer_exactly_once():
    plan = plan_layers(
        total_layers=26,
        head_free_mb=4000,
        worker_free_mb={"a": 3000, "b": 2000},
        bytes_per_layer_mb=20.0,
        worker_order=["a", "b"],
    )
    covered = []
    for a in plan.assignments:
        covered.extend(range(a.layer_start, a.layer_end + 1))
    assert sorted(covered) == list(range(26))
    assert len(covered) == len(set(covered))


def test_layers_split_in_proportion_to_device_capacity():
    # Head cap = 4000*0.7/20 = 140 (capped 26), worker a cap = 26 -> equal shares of 26 across 2 devices.
    plan = plan_layers(26, head_free_mb=4000, worker_free_mb={"a": 4000}, bytes_per_layer_mb=20.0,
                       worker_order=["a"])
    assert plan.layers_for("a") + plan.layers_for(HEAD) == 26
    assert abs(plan.layers_for("a") - plan.layers_for(HEAD)) <= 1


def test_every_measured_worker_gets_a_share():
    # The bug this guards against: greedy filling gave the first worker everything and dropped the rest.
    plan = plan_layers(26, head_free_mb=7000, worker_free_mb={"a": 7000, "b": 7000}, bytes_per_layer_mb=20.0,
                       worker_order=["a", "b"])
    assert plan.layers_for("a") > 0
    assert plan.layers_for("b") > 0
    assert plan.dropped_workers == ()
    assert sum(plan.layers_for(d) for d in ("a", "b", HEAD)) == 26


def test_small_worker_is_dropped_and_head_absorbs_layers():
    # 10 MB free * 0.7 / 20 = 0 capacity -> dropped; its share returns to the head.
    plan = plan_layers(26, head_free_mb=2000, worker_free_mb={"tiny": 10.0}, bytes_per_layer_mb=20.0,
                       worker_order=["tiny"])
    assert plan.dropped_workers == ("tiny",)
    assert plan.layers_for(HEAD) == 26
    assert plan.device_order == ()


def test_head_only_when_no_workers():
    plan = plan_layers(26, head_free_mb=2000, worker_free_mb={}, bytes_per_layer_mb=20.0)
    assert plan.assignments == (plan.assignments[0],)
    assert plan.assignments[0].device == HEAD
    assert plan.tensor_split == []


def test_head_too_small_for_its_share_is_rejected():
    # Head cap = 100*0.7/20 = 3 layers, but it must hold all 26 with no workers.
    with pytest.raises(ValueError, match="can hold 3 of 26"):
        plan_layers(26, head_free_mb=100, worker_free_mb={}, bytes_per_layer_mb=20.0)


def test_tensor_split_is_head_first_then_workers_in_rpc_order():
    plan = plan_layers(
        26,
        head_free_mb=4000,
        worker_free_mb={"a": 4000},
        bytes_per_layer_mb=20.0,
        worker_order=["a"],
    )
    split = plan.tensor_split
    head_n, a_n = plan.layers_for(HEAD), plan.layers_for("a")
    assert split == [round(head_n / 26, 4), round(a_n / 26, 4)]
    assert abs(sum(split) - 1.0) < 1e-3


def test_head_command_uses_active_workers_and_split():
    cfg = ClusterConfig(
        model_path="m.gguf",
        workers=[WorkerEndpoint("a", "100.1.1.1", 50052), WorkerEndpoint("b", "100.1.1.2", 50052)],
    )
    plan = plan_layers(26, head_free_mb=2000, worker_free_mb={"a": 3000}, bytes_per_layer_mb=20.0,
                       worker_order=["a"])
    active = [cfg.workers[0]]
    cmd = build_head_command(cfg, "llama-server", "k", plan=plan, active_workers=active)
    assert cmd[cmd.index("--rpc") + 1] == "100.1.1.1:50052"
    assert cmd[cmd.index("-dev") + 1] == "RPC0"
    assert cmd[cmd.index("-ngl") + 1] == str(plan.total_layers - plan.layers_for(HEAD))
    assert cmd[cmd.index("-ts") + 1] == "1.0"


class _FakeHead:
    def __init__(self):
        self.restarts = []
        self.running = False
        self.last_load = {}

    def is_running(self):
        return self.running

    def restart(self, plan, active_workers, startup_timeout=300.0, stall_timeout=0.0):
        self.restarts.append([w.name for w in active_workers])
        self.running = True

    def status(self):
        return {}


def _probe_factory(free_by_addr, local_free=8000.0):
    def probe(_cli, endpoints):
        rpc = {e: free_by_addr[e] for e in endpoints if e in free_by_addr}
        return ProbeResult(local_free_mb=local_free, rpc_free_mb=rpc)
    return probe


def _cfg(tmp_path, workers, policy="always", model_bytes=1024):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"\0" * model_bytes)  # tiny file: the supervisor tests inject probe memory, not model size
    return ClusterConfig(
        model_path=str(model), total_layers=26, workers=workers, worker_layer_policy=policy
    )


def test_supervisor_excludes_high_latency_worker(tmp_path):
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers)
    sup = ClusterSupervisor(
        cfg, _FakeHead(),
        probe=_probe_factory({"100.1.1.1:50052": 3000.0}),
        reachable=lambda h, p: True,
        latency=lambda h, p: 228.0,
    )
    assert sup._update_reachability() == []


def test_supervisor_can_force_slow_worker(tmp_path):
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers)
    cfg.allow_slow_workers = True
    sup = ClusterSupervisor(
        cfg, _FakeHead(),
        probe=_probe_factory({"100.1.1.1:50052": 3000.0}),
        reachable=lambda h, p: True,
        latency=lambda h, p: 228.0,
    )
    assert sup._update_reachability() == workers


def test_supervisor_does_not_restart_when_membership_stable(tmp_path):
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers)
    head = _FakeHead()
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 3000.0}),
        reachable=lambda h, p: True,
        latency=lambda h, p: 0.0,
    )
    assert sup.reconcile() is not None
    assert sup.reconcile() is None
    assert len(head.restarts) == 1


def test_supervisor_replans_after_worker_drops(tmp_path):
    # Each worker holds 7 layers (200 MB free * 0.7 / 20), so both are used and the head keeps 12.
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052), WorkerEndpoint("b", "100.1.1.2", 50052)]
    cfg = _cfg(tmp_path, workers)
    head = _FakeHead()
    up = {"100.1.1.1:50052", "100.1.1.2:50052"}
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 200.0, "100.1.1.2:50052": 200.0}),
        reachable=lambda h, p: f"{h}:{p}" in up,
        latency=lambda h, p: 0.0,
        failures_before_drop=2,
    )
    sup.reconcile()
    assert head.restarts[-1] == ["a", "b"]

    up.discard("100.1.1.2:50052")
    sup.reconcile()  # first failure: still counted as live, no replan
    assert len(head.restarts) == 1
    sup.reconcile()  # second failure: dropped, head replans with worker a only
    assert head.restarts[-1] == ["a"]


def test_supervisor_restores_worker_on_recovery(tmp_path):
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers)
    head = _FakeHead()
    up = {"100.1.1.1:50052"}
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 3000.0}),
        reachable=lambda h, p: f"{h}:{p}" in up,
        latency=lambda h, p: 0.0,
        failures_before_drop=1,
    )
    sup.reconcile()
    up.clear()
    sup.reconcile()
    assert head.restarts[-1] == []
    up.add("100.1.1.1:50052")
    sup.reconcile()
    assert head.restarts[-1] == ["a"]


def test_planner_caps_layers_per_worker():
    plan = plan_layers(
        26, head_free_mb=8000, worker_free_mb={"a": 8000}, bytes_per_layer_mb=20.0,
        worker_order=["a"], max_worker_layers=3,
    )
    assert plan.layers_for("a") <= 3
    assert plan.layers_for("a") + plan.layers_for(HEAD) == 26


def test_planner_cap_of_one_keeps_a_small_worker():
    # A cap below the default minimum must still be honored, otherwise the cap would be unusable.
    plan = plan_layers(
        26, head_free_mb=8000, worker_free_mb={"a": 8000}, bytes_per_layer_mb=20.0,
        worker_order=["a"], max_worker_layers=1,
    )
    assert plan.layers_for("a") == 1
    assert plan.dropped_workers == ()


def test_planner_rejects_negative_cap():
    with pytest.raises(ValueError, match="max_worker_layers"):
        plan_layers(
            26, head_free_mb=8000, worker_free_mb={"a": 8000}, bytes_per_layer_mb=20.0,
            worker_order=["a"], max_worker_layers=-1,
        )


def test_auto_policy_keeps_workers_out_when_the_head_holds_the_model(tmp_path):
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers, policy="auto")
    head = _FakeHead()
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 8000.0}, local_free=8000.0),
        reachable=lambda h, p: True,
        latency=lambda h, p: 0.0,
    )
    plan = sup.reconcile()
    assert plan.device_order == ()
    assert plan.layers_for(HEAD) == 26
    assert head.restarts[-1] == []


def test_auto_policy_splits_when_the_head_is_too_small(tmp_path):
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    # 3 MB model / 26 layers = ~0.115 MB per layer; a 1 MB head cannot hold it.
    cfg = _cfg(tmp_path, workers, policy="auto", model_bytes=3_000_000)
    head = _FakeHead()
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 8000.0}, local_free=1.0),
        reachable=lambda h, p: True,
        latency=lambda h, p: 0.0,
    )
    plan = sup.reconcile()
    assert plan.device_order == ("a",)
    assert plan.layers_for("a") > 0
    assert head.restarts[-1] == ["a"]


def test_auto_policy_does_not_restart_when_the_split_is_unchanged(tmp_path):
    # Regression: the head restarted every probe interval because live membership (the worker)
    # was compared against the active split (empty). A live worker must not bounce a head-only head.
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers, policy="auto")
    head = _FakeHead()
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 8000.0}),
        reachable=lambda h, p: True,
        latency=lambda h, p: 0.0,
    )
    assert sup.reconcile() is not None
    assert sup.reconcile() is None
    assert len(head.restarts) == 1


def test_supervisor_never_splits_under_never_policy(tmp_path):
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers, policy="never")
    head = _FakeHead()
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 8000.0}),
        reachable=lambda h, p: True,
        latency=lambda h, p: 0.0,
    )
    plan = sup.reconcile()
    assert plan.device_order == ()
    assert head.restarts[-1] == []


def test_never_policy_does_not_fall_back_to_a_split(tmp_path):
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers, policy="never", model_bytes=3_000_000)
    head = _FakeHead()
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 8000.0}, local_free=1.0),
        reachable=lambda h, p: True,
        latency=lambda h, p: 0.0,
    )
    with pytest.raises(ValueError, match="can hold"):
        sup.reconcile()
    assert head.restarts == []


def test_supervisor_drops_the_heads_own_rpc_endpoint(tmp_path):
    workers = [WorkerEndpoint("self", "127.0.0.1", 50052),
               WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers)
    sup = ClusterSupervisor(
        cfg, _FakeHead(),
        probe=_probe_factory({"100.1.1.1:50052": 3000.0}),
        reachable=lambda h, p: True,
        latency=lambda h, p: 0.0,
    )
    assert list(sup._states) == ["a"]
    assert [w.name for w in cfg.workers] == ["a"]


def test_supervisor_records_measured_rate_and_caps_the_next_split(tmp_path, monkeypatch):
    monkeypatch.setattr("racon.cluster.supervisor.bytes_per_layer_mb", lambda *_: 20.0)
    workers = [WorkerEndpoint("a", "100.1.1.1", 50052)]
    cfg = _cfg(tmp_path, workers, policy="always")
    cfg.max_worker_layers = 0  # let the measured rate decide
    cfg.rpc_startup_timeout_s = 120.0
    head = _FakeHead()
    rates = tmp_path / "rates.json"
    sup = ClusterSupervisor(
        cfg, head,
        probe=_probe_factory({"100.1.1.1:50052": 8000.0}),
        reachable=lambda h, p: True,
        latency=lambda h, p: 0.0,
        rates_path=rates,
    )
    # 20 MiB over 20 s: 1 MiB/s, so 120 s * 0.8 at 20 MiB/layer fits 4 layers.
    head.last_load = {"seconds": 20.0, "rpc_mb": 20.0, "tensors_transferred": 10}
    plan = sup.reconcile()
    assert plan is not None and plan.layers_for("a") > 0
    assert rates.exists()
    assert "100.1.1.1:50052" in rates.read_text(encoding="utf-8")

    capped, active = sup.build_plan(workers)
    assert capped.layers_for("a") <= 4
    assert capped.layers_for("a") < plan.layers_for("a")
    assert active == workers

