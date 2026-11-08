"""Node hardware capabilities are persisted, refreshed and returned by the DB layer."""
from backend.racon.hardware import friendly_os_name


def _node(**overrides):
    node = {
        "id": "node-test",
        "name": "Ubuntu box",
        "is_local": False,
        "status": "available",
        "host": "192.168.1.16",
        "ram_total_mb": 15895.0,
        "ram_available_mb": 12000.0,
        "capabilities": {
            "cpu_name": "Intel(R) Core(TM) i5-3210M CPU @ 2.50GHz",
            "cpu_logical_count": 4,
            "gpu": {"name": "Intel 3rd Gen Core processor Graphics", "vram_total_mb": None},
            "runtimes": ["ollama", "local"],
            "os": "Ubuntu 24.04.4 LTS",
            "rpc_endpoint": "192.168.1.16:50052",
            "models": ["qwen2.5-0.5b"],
            "features": ["text_generation"],
        },
    }
    node.update(overrides)
    return node


def test_upsert_node_returns_real_hardware(isolated_db):
    isolated_db.upsert_node(_node())

    stored = isolated_db.get_node("node-test")
    caps = stored["capabilities"]

    assert caps["cpu_name"].startswith("Intel(R) Core")
    assert caps["cpu_logical_count"] == 4
    assert caps["gpu"]["name"].startswith("Intel 3rd Gen")
    assert caps["runtimes"] == ["ollama", "local"]
    assert caps["os"] == "Ubuntu 24.04.4 LTS"
    assert caps["rpc_endpoint"] == "192.168.1.16:50052"
    assert caps["models"] == ["qwen2.5-0.5b"]
    assert caps["features"] == ["text_generation"]
    assert stored["ram_total_mb"] == 15895.0


def test_second_upsert_refreshes_capabilities(isolated_db):
    isolated_db.upsert_node(_node())

    refreshed = _node(
        name="Ubuntu box (renamed)",
        ram_available_mb=6400.0,
        capabilities={
            "cpu_name": "AMD Ryzen 7 5800X",
            "cpu_logical_count": 16,
            "gpu": {"name": "NVIDIA GeForce RTX 3060", "vram_total_mb": 12288.0},
            "runtimes": ["ollama", "local", "llama_cpp"],
            "os": "Ubuntu 24.04.4 LTS",
            "models": ["llama-3.2-1b"],
            "features": ["text_generation", "embeddings"],
        },
    )
    isolated_db.upsert_node(refreshed)

    stored = isolated_db.get_node("node-test")
    caps = stored["capabilities"]

    assert stored["name"] == "Ubuntu box (renamed)"
    assert caps["cpu_name"] == "AMD Ryzen 7 5800X"
    assert caps["cpu_logical_count"] == 16
    assert caps["gpu"]["name"] == "NVIDIA GeForce RTX 3060"
    assert caps["gpu"]["vram_total_mb"] == 12288.0
    assert caps["runtimes"] == ["ollama", "local", "llama_cpp"]
    assert caps["models"] == ["llama-3.2-1b"]
    assert caps["features"] == ["text_generation", "embeddings"]
    assert stored["ram_available_mb"] == 6400.0


def test_friendly_os_name_is_not_a_kernel_build_string():
    name = friendly_os_name()
    assert name
    assert "SMP PREEMPT" not in name
    assert len(name) <= 128
