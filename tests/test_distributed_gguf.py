"""Tests for GGUF model sharding, partition manifests, and distributed RPC layer execution (ADR-012)."""
import json
import os
import pytest

from racon.runtime.distributed_gguf import (
    GgufShardManifest,
    ShardInfo,
    compute_file_sha256,
    find_llama_tool,
    inspect_gguf_metadata,
    verify_shard,
)
from racon.runtime.rpc_worker import (
    DistributedPipelineEngine,
    RpcServerProcess,
)
from protocols import TaskType
from racon.agent.worker import RaconAgent

MODEL_PATH = r"F:\RACoN\Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_Q4_k_m.gguf"
SHARDS_DIR = r"F:\RACoN\models\shards\gemma-3-1b"


def test_find_llama_tools():
    """Verify official llama.cpp binaries are discovered."""
    split_tool = find_llama_tool("llama-gguf-split")
    rpc_tool = find_llama_tool("rpc-server")
    cli_tool = find_llama_tool("llama-cli")

    assert split_tool is not None, "llama-gguf-split binary not found"
    assert os.path.isfile(split_tool)
    assert rpc_tool is not None, "rpc-server binary not found"
    assert os.path.isfile(rpc_tool)
    assert cli_tool is not None, "llama-cli binary not found"
    assert os.path.isfile(cli_tool)


def test_inspect_gguf_metadata():
    """Verify GGUF inspection accurately extracts Gemma-3-1B architecture and tensors."""
    if not os.path.exists(MODEL_PATH):
        pytest.skip(f"Model file not present at {MODEL_PATH}")

    meta = inspect_gguf_metadata(MODEL_PATH)
    assert meta["is_valid_gguf"] is True
    assert meta["architecture"] == "gemma3"
    assert meta["block_count"] == 26
    assert meta["tensor_count"] == 340
    assert meta["file_size_bytes"] == os.path.getsize(MODEL_PATH)


def test_manifest_serialization(tmp_path):
    """Verify GgufShardManifest round-trip serialization."""
    manifest = GgufShardManifest(
        model_name="gemma-3-1b-test",
        architecture="gemma3",
        total_layers=26,
        total_tensors=340,
        original_file_size=806059360,
        original_sha256="abc123def456",
        split_strategy="size_450MB",
        created_at=1700000000.0,
        shards=[
            ShardInfo(
                shard_index=1,
                total_shards=2,
                filename="shard-1.gguf",
                file_path=str(tmp_path / "shard-1.gguf"),
                file_size_bytes=451855520,
                sha256="111122223333",
                tensor_count=90,
                layer_start=0,
                layer_end=12,
                min_ram_mb=540.0,
            ),
            ShardInfo(
                shard_index=2,
                total_shards=2,
                filename="shard-2.gguf",
                file_path=str(tmp_path / "shard-2.gguf"),
                file_size_bytes=354204064,
                sha256="444455556666",
                tensor_count=250,
                layer_start=13,
                layer_end=25,
                min_ram_mb=425.0,
            ),
        ],
    )

    out_file = tmp_path / "manifest.json"
    with open(out_file, "w") as f:
        json.dump(manifest.to_dict(), f)

    with open(out_file, "r") as f:
        loaded_dict = json.load(f)

    loaded = GgufShardManifest.from_dict(loaded_dict)
    assert loaded.model_name == "gemma-3-1b-test"
    assert len(loaded.shards) == 2
    assert loaded.shards[0].layer_start == 0
    assert loaded.shards[0].layer_end == 12
    assert loaded.shards[1].layer_start == 13
    assert loaded.shards[1].layer_end == 25


def test_existing_physical_shards_and_manifest():
    """Verify the pre-generated Gemma-3-1B physical shards and their cryptographic integrity."""
    shard1 = os.path.join(SHARDS_DIR, "gemma-3-1b-split.gguf-00001-of-00002.gguf")
    shard2 = os.path.join(SHARDS_DIR, "gemma-3-1b-split.gguf-00002-of-00002.gguf")
    manifest_path = os.path.join(SHARDS_DIR, "shards_manifest.json")

    if not (os.path.exists(shard1) and os.path.exists(shard2) and os.path.exists(manifest_path)):
        pytest.skip("Physical shards or manifest not generated yet.")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)
    manifest = GgufShardManifest.from_dict(manifest_data)

    assert manifest.total_layers == 26
    assert len(manifest.shards) == 2

    # Verify cryptographic hashes of the physical files
    s1_info = manifest.shards[0]
    assert verify_shard(shard1, s1_info.sha256) is True

    s2_info = manifest.shards[1]
    assert verify_shard(shard2, s2_info.sha256) is True


def test_rpc_server_process_lifecycle():
    """Verify starting and stopping an RPC server process on loopback."""
    test_port = 50058
    rpc = RpcServerProcess(host="127.0.0.1", port=test_port, threads=2, enable_cache=False)
    assert not rpc.is_running()

    started = rpc.start()
    assert started is True
    assert rpc.is_running() is True

    rpc.stop()
    assert rpc.is_running() is False


def test_agent_rpc_worker_integration(tmp_path):
    """Verify RaconAgent manages RPC worker state cleanly."""
    agent = RaconAgent(coordinator_url="http://127.0.0.1:8765")
    test_port = 50059

    ok = agent.start_rpc_worker(host="127.0.0.1", port=test_port, threads=2)
    assert ok is True
    assert agent.rpc_endpoint == f"127.0.0.1:{test_port}"
    status = agent.get_status()
    assert status["rpc_endpoint"] == f"127.0.0.1:{test_port}"

    agent.stop_rpc_worker()
    assert agent.rpc_endpoint is None
    status = agent.get_status()
    assert status["rpc_endpoint"] is None


def test_agent_shard_verification_task():
    """Verify agent correctly executes SHARD_VERIFICATION allow-listed task."""
    shard1 = os.path.join(SHARDS_DIR, "gemma-3-1b-split.gguf-00001-of-00002.gguf")
    manifest_path = os.path.join(SHARDS_DIR, "shards_manifest.json")
    if not (os.path.exists(shard1) and os.path.exists(manifest_path)):
        pytest.skip("Physical shards or manifest not generated yet.")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)
    s1_sha = manifest_data["shards"][0]["sha256"]

    agent = RaconAgent(coordinator_url="http://127.0.0.1:8765")
    res, err = agent._execute_task_workload(
        task_type=TaskType.SHARD_VERIFICATION.value,
        model_name="gemma-3-1b",
        parameters={
            "shard_path": shard1,
            "expected_sha256": s1_sha,
        },
    )
    assert err is None
    assert res["valid"] is True

    # Bad sha test
    res_bad, err_bad = agent._execute_task_workload(
        task_type=TaskType.SHARD_VERIFICATION.value,
        model_name="gemma-3-1b",
        parameters={
            "shard_path": shard1,
            "expected_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        },
    )
    assert err_bad is not None
    assert res_bad["valid"] is False


def test_distributed_layer_inference_e2e():
    """Verify live end-to-end distributed layer execution over RPC with Gemma-3-1B."""
    if not os.path.exists(MODEL_PATH):
        pytest.skip(f"Model file not present at {MODEL_PATH}")

    port = 50060
    rpc = RpcServerProcess(host="127.0.0.1", port=port, threads=4, enable_cache=False)
    started = rpc.start()
    assert started is True

    try:
        engine = DistributedPipelineEngine()
        result = engine.execute(
            model_path=MODEL_PATH,
            rpc_endpoints=[f"127.0.0.1:{port}"],
            prompt="2+2=",
            max_tokens=3,
            n_gpu_layers=13,
            timeout_seconds=90.0,
        )

        assert result.exit_code == 0, f"Distributed inference failed: {result.error}"
        assert result.mode == "OBSERVED"
        assert len(result.response) > 0
        assert f"127.0.0.1:{port}" in result.rpc_endpoints
        assert len(result.memory_breakdown) > 0
    finally:
        rpc.stop()


def test_select_optimal_shard():
    """Verify optimal layer selection based on node role and memory constraints."""
    from racon.runtime.distributed_gguf import select_optimal_shard, load_manifest
    manifest = load_manifest(SHARDS_DIR)
    assert manifest is not None
    assert manifest.hf_repo_id == "toriqulhaque/RACoN-Gemma-3-1B-Shards"

    # Master node gets Shard 1 (embeddings + early layers)
    master_shard = select_optimal_shard(manifest, available_ram_mb=8192.0, is_master=True)
    assert master_shard.shard_index == 1
    assert master_shard.layer_start == 0

    # Worker node gets Shard 2 (late layers + head)
    worker_shard = select_optimal_shard(manifest, available_ram_mb=4096.0, is_master=False)
    assert worker_shard.shard_index == 2
    assert worker_shard.layer_start == 13


def test_ensure_worker_shard():
    """Verify discovering and resolving the worker shard from local cache or manifest."""
    from racon.runtime.distributed_gguf import ensure_worker_shard
    shard, path = ensure_worker_shard(manifest_source=SHARDS_DIR, available_ram_mb=4096.0, is_master=False)
    assert shard.shard_index == 2
    assert os.path.isfile(path)


def test_agent_prepare_and_start_rpc_worker():
    """Verify worker agent prepares shard and starts RPC server."""
    agent = RaconAgent(node_id_override="test-shard-worker")
    port = 50062
    ok, msg = agent.prepare_and_start_rpc_worker(host="127.0.0.1", port=port, threads=2)
    try:
        assert ok is True
        assert agent.rpc_endpoint == f"127.0.0.1:{port}"
        status = agent.get_status()
        assert status["assigned_shard"] is not None
        assert "gemma-3-1b-split.gguf-00002" in status["assigned_shard"]
    finally:
        agent.stop_rpc_worker()

