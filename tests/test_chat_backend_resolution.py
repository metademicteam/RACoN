"""Local model discovery and remote gateway configuration for the chat panel."""
import json

from racon.ui import chat_panel


def test_resolve_local_model_prefers_env(tmp_path, monkeypatch):
    model = tmp_path / "custom.gguf"
    model.write_bytes(b"gguf")
    monkeypatch.setenv("RACON_GGUF_MODEL", str(model))
    assert chat_panel.resolve_local_model_path() == str(model)


def test_resolve_local_model_uses_models_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("RACON_GGUF_MODEL", raising=False)
    monkeypatch.setattr(chat_panel, "DEFAULT_GGUF_MODEL", str(tmp_path / "missing.gguf"))
    first = tmp_path / "models" / "shards" / "gemma-3-1b-split.gguf-00001-of-00002.gguf"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"gguf")
    (first.parent / "gemma-3-1b-split.gguf-00002-of-00002.gguf").write_bytes(b"gguf")

    import racon.config
    monkeypatch.setattr(racon.config, "get_data_dir", lambda: tmp_path)

    assert chat_panel.resolve_local_model_path() == str(first)


def test_resolve_local_model_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("RACON_GGUF_MODEL", raising=False)
    monkeypatch.setattr(chat_panel, "DEFAULT_GGUF_MODEL", str(tmp_path / "missing.gguf"))
    import racon.config
    monkeypatch.setattr(racon.config, "get_data_dir", lambda: tmp_path / "data")
    assert chat_panel.resolve_local_model_path() is None


def test_gateway_config_from_env(monkeypatch):
    monkeypatch.setenv("RACON_GATEWAY_URL", "https://gw.example/")
    monkeypatch.setenv("RACON_API_KEY", "rk_test")
    assert chat_panel.gateway_config() == ("https://gw.example", "rk_test")


def test_gateway_config_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("RACON_GATEWAY_URL", raising=False)
    monkeypatch.delenv("RACON_API_KEY", raising=False)
    (tmp_path / "gateway.json").write_text(
        json.dumps({"url": "https://racon.metademic.org", "api_key": "rk_file"}),
        encoding="utf-8",
    )
    import racon.config
    monkeypatch.setattr(racon.config, "get_data_dir", lambda: tmp_path)
    assert chat_panel.gateway_config() == ("https://racon.metademic.org", "rk_file")


def test_gateway_config_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("RACON_GATEWAY_URL", raising=False)
    monkeypatch.delenv("RACON_API_KEY", raising=False)
    import racon.config
    monkeypatch.setattr(racon.config, "get_data_dir", lambda: tmp_path)
    assert chat_panel.gateway_config() == (None, None)
