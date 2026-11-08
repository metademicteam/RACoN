"""Hugging Face catalog, discovery, and download-routing tests."""
from __future__ import annotations

import asyncio

import pytest

from racon.models.catalog import build_catalog, find_entry
from racon.models.huggingface import (
    HuggingFaceClient,
    HuggingFaceError,
    build_entry,
    validate_repo_id,
    validate_revision,
)


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        return self._response


def _client_returning(payload, status=200):
    return HuggingFaceClient(client_factory=lambda: _FakeAsyncClient(_Response(status, payload)))


def test_repo_and_revision_validation():
    assert validate_repo_id("bartowski/Model-GGUF") == "bartowski/Model-GGUF"
    assert validate_revision("main") == "main"
    for bad in ("", "no-slash", "../evil/repo", "owner/", "owner/repo?x=1"):
        with pytest.raises(HuggingFaceError):
            validate_repo_id(bad)
    with pytest.raises(HuggingFaceError):
        validate_revision("../main")


def test_list_gguf_files_extracts_lfs_sha_and_https_url():
    payload = [
        {"type": "file", "path": "README.md", "size": 100},
        {
            "type": "file",
            "path": "Model-Q4_K_M.gguf",
            "size": 1000,
            "lfs": {"oid": "a" * 64, "size": 1000},
        },
        {"type": "directory", "path": "subdir"},
    ]
    client = _client_returning(payload)
    files = asyncio.run(client.list_gguf_files("owner/repo"))
    assert len(files) == 1
    item = files[0]
    assert item.path == "Model-Q4_K_M.gguf"
    assert item.sha256 == "a" * 64
    assert item.size_bytes == 1000
    assert item.download_url == (
        "https://huggingface.co/owner/repo/resolve/main/Model-Q4_K_M.gguf"
    )


def test_list_gguf_files_reports_gated_repo():
    client = _client_returning({}, status=403)
    with pytest.raises(HuggingFaceError, match="denied"):
        asyncio.run(client.list_gguf_files("owner/private-repo"))


def test_build_entry_uses_filename_stem_and_verified_artifact():
    from racon.models.huggingface import HfFile

    file = HfFile(
        repo_id="owner/repo",
        path="folder/Model-Q4_K_M.gguf",
        size_bytes=1234,
        sha256="b" * 64,
        download_url="https://huggingface.co/owner/repo/resolve/main/folder/Model-Q4_K_M.gguf",
    )
    entry = build_entry(file)
    assert entry.id == "Model-Q4_K_M"
    assert entry.runtime == "llama_cpp"
    assert entry.source == "huggingface"
    assert entry.files[0].sha256 == "b" * 64
    assert entry.files[0].url.startswith("https://huggingface.co/")


def test_builtin_huggingface_entries_are_downloadable_and_verified():
    entries = [entry for entry in build_catalog() if entry.source == "huggingface"]
    assert len(entries) >= 3
    for entry in entries:
        assert entry.download_url and entry.download_url.startswith("https://huggingface.co/")
        assert entry.files[0].sha256 and len(entry.files[0].sha256) == 64
        assert entry.files[0].size_bytes and entry.files[0].size_bytes > 0
        assert entry.min_ram_mb and entry.min_ram_mb > 0


def test_find_entry_matches_installed_model_id_for_huggingface_entries():
    entry = find_entry(build_catalog(), "Qwen2.5-0.5B-Instruct-Q4_K_M")
    assert entry is not None
    assert entry.source == "huggingface"
    assert entry.files[0].filename == "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"


def test_recommendations_include_downloadable_huggingface_entries():
    from fastapi.testclient import TestClient

    from backend.racon.api.app import app

    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        data = client.get("/api/v1/models/recommendations").json()

    hf_entries = [
        item for item in data["recommendations"] if item["source"] == "huggingface"
    ]
    assert hf_entries
    for entry in hf_entries:
        assert entry["downloadable"] is True
        assert entry["repo_id"] and "/" in entry["repo_id"]
        assert entry["filename"].endswith(".gguf")
        assert entry["sha256"] and len(entry["sha256"]) == 64
        assert entry["fits"] in (True, False, None)


def test_pull_routes_huggingface_entries_to_the_model_manager(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.racon.api import routes_models
    from backend.racon.api.app import app

    started: list[str] = []

    def fake_download(entry):
        started.append(entry.id)

    monkeypatch.setattr(routes_models, "_run_gguf_download", fake_download)
    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        response = client.post(
            "/api/v1/models/pull",
            json={"model_name": "Qwen2.5-0.5B-Instruct-Q4_K_M"},
        )
    assert response.status_code == 200
    assert response.json()["source"] == "huggingface"
    assert started == ["Qwen2.5-0.5B-Instruct-Q4_K_M"]


def test_pull_refuses_local_only_model():
    from fastapi.testclient import TestClient

    from backend.racon.api.app import app

    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        response = client.post("/api/v1/models/pull", json={"model_name": "gemma-3-1b-racon"})
    assert response.status_code == 400
    assert "local-only" in str(response.json().get("error", ""))


def test_hf_files_endpoint_rejects_invalid_repo():
    from fastapi.testclient import TestClient

    from backend.racon.api.app import app

    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        response = client.get("/api/v1/models/hf/files", params={"repo_id": "../evil"})
    assert response.status_code == 400
