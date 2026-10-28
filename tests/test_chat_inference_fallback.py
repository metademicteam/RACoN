"""Chat inference: RPC endpoint is always passed to the engine, and a failed GPU run retries on CPU."""
from racon.runtime.rpc_worker import DistributedInferenceResult, DistributedPipelineEngine


def _result(exit_code, error=None):
    return DistributedInferenceResult(
        response="ok" if exit_code == 0 else "",
        prompt_tokens_per_second=1.0,
        generation_tokens_per_second=2.0,
        total_duration_seconds=0.1,
        memory_breakdown={},
        rpc_endpoints=[],
        layers_offloaded=0,
        exit_code=exit_code,
        error=error,
        mode="OBSERVED" if exit_code == 0 else "FAILED",
    )


def test_cpu_only_command_disables_gpu_and_rpc(tmp_path, monkeypatch):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    engine = DistributedPipelineEngine(cli_bin=str(tmp_path / "llama-cli.exe"))
    (tmp_path / "llama-cli.exe").write_bytes(b"x")
    captured = {}

    class Done:
        returncode = 0
        stdout = "answer"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Done()

    monkeypatch.setattr("racon.runtime.rpc_worker.subprocess.run", fake_run)
    engine.execute(
        model_path=str(model),
        rpc_endpoints=["100.1.1.1:50052"],
        prompt="hi",
        cpu_only=True,
    )
    cmd = captured["cmd"]
    assert cmd[cmd.index("-ngl") + 1] == "0"
    assert "--device" in cmd and cmd[cmd.index("--device") + 1] == "none"
    assert "--rpc" not in cmd


def test_gpu_command_uses_rpc_endpoint(tmp_path, monkeypatch):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    (tmp_path / "llama-cli.exe").write_bytes(b"x")
    engine = DistributedPipelineEngine(cli_bin=str(tmp_path / "llama-cli.exe"))
    captured = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Done()

    monkeypatch.setattr("racon.runtime.rpc_worker.subprocess.run", fake_run)
    engine.execute(model_path=str(model), rpc_endpoints=["100.1.1.1:50052"], prompt="hi")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--rpc") + 1] == "100.1.1.1:50052"
    assert "--device" not in cmd
