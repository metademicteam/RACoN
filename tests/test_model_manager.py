"""Tests for local model storage: verification, disk guards and deletion rules."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path

import httpx
import pytest

from racon.models.catalog import CatalogEntry, ModelFile, build_catalog, find_entry
from racon.models.manager import (
    InsufficientDiskSpaceError,
    ModelManager,
    ModelStorageError,
    ModelVerificationError,
    UntrustedSourceError,
)


def _write_gguf(path: Path, payload: bytes = b"GGUF" + b"\x00" * 64) -> bytes:
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_install_from_path_copies_into_managed_dir(tmp_path):
    source = tmp_path / "incoming" / "model.gguf"
    source.parent.mkdir()
    _write_gguf(source)

    manager = ModelManager(tmp_path / "store")
    result = manager.install_from_path(source)

    assert result.verified is False
    assert result.model.path.parent == (tmp_path / "store").resolve()
    assert result.model.size_bytes == source.stat().st_size
    assert source.exists()  # copy, not move


def test_install_from_path_verifies_supplied_digest(tmp_path):
    source = tmp_path / "model.gguf"
    _write_gguf(source)
    manager = ModelManager(tmp_path / "store")

    with pytest.raises(ModelVerificationError):
        manager.install_from_path(source, expected_sha256="0" * 64)


def test_install_rejects_non_gguf(tmp_path):
    bad = tmp_path / "model.bin"
    bad.write_text("not a model")
    manager = ModelManager(tmp_path / "store")
    with pytest.raises(ModelStorageError):
        manager.install_from_path(bad)


def test_disk_guard_blocks_install(tmp_path, monkeypatch):
    source = tmp_path / "model.gguf"
    _write_gguf(source)
    manager = ModelManager(tmp_path / "store")

    class _Usage:
        free = 10 * 1024 * 1024  # 10 MB free

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _Usage())
    with pytest.raises(InsufficientDiskSpaceError):
        manager.install_from_path(source)


def _entry(url: str, sha: str | None = None) -> CatalogEntry:
    return CatalogEntry(
        id="test-model",
        display_name="Test",
        runtime="llama_cpp",
        files=[ModelFile(filename="test.gguf", url=url, sha256=sha)],
    )


def test_download_refuses_plain_http(tmp_path):
    manager = ModelManager(tmp_path / "store")
    with pytest.raises(UntrustedSourceError):
        asyncio.run(manager.download(_entry("http://example.com/test.gguf", "a" * 64)))


def test_download_refuses_unverified_artifact(tmp_path):
    manager = ModelManager(tmp_path / "store")
    with pytest.raises(UntrustedSourceError):
        asyncio.run(manager.download(_entry("https://example.com/test.gguf", None)))


def test_download_verifies_sha256_and_rejects_mismatch(tmp_path):
    payload = b"GGUF" + b"\x01" * 128

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    manager = ModelManager(tmp_path / "store")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ModelVerificationError):
        asyncio.run(
            manager.download(_entry("https://example.com/test.gguf", "b" * 64), client=client)
        )
    assert not (tmp_path / "store" / "test.gguf").exists()


def test_download_writes_verified_file(tmp_path):
    payload = b"GGUF" + b"\x02" * 128
    digest = hashlib.sha256(payload).hexdigest()
    progress: list[tuple[int, int | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"Content-Length": str(len(payload))})

    manager = ModelManager(tmp_path / "store")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(
        manager.download(
            _entry("https://example.com/test.gguf", digest),
            client=client,
            progress=lambda done, total: progress.append((done, total)),
        )
    )

    assert result.verified is True
    assert result.model.path.read_bytes() == payload
    assert progress and progress[-1][0] == len(payload)


def test_delete_refuses_files_outside_the_managed_directory(tmp_path, monkeypatch):
    external = tmp_path / "elsewhere" / "outside.gguf"
    external.parent.mkdir()
    _write_gguf(external)

    manager = ModelManager(tmp_path / "store")
    monkeypatch.setattr(manager, "installed", lambda: [])
    monkeypatch.setattr(manager, "find_installed", lambda _mid: type("M", (), {"path": external})())

    with pytest.raises(ModelStorageError):
        manager.delete("outside")


def test_estimate_uses_gguf_header_geometry(tmp_path):
    # Minimal GGUF header: magic, version, tensor_count=0, kv_count=0
    import struct

    header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 0)
    model = tmp_path / "tiny.gguf"
    model.write_bytes(header + b"\x00" * (2 * 1024 * 1024))

    manager = ModelManager(tmp_path / "store")
    estimate = manager.estimate(str(model))
    assert estimate["weights_mb"] > 0
    # A header without attention geometry must not invent a KV-cache figure.
    assert estimate["kv_cache_mb"] is None
    assert estimate["confident"] is False


def test_catalog_seeds_and_operator_override(tmp_path):
    operator = tmp_path / "catalog.json"
    operator.write_text(
        """[{"id":"ops-model","runtime":"llama_cpp","files":[{"filename":"ops.gguf"}]}]""",
        encoding="utf-8",
    )
    entries = build_catalog(operator)
    assert find_entry(entries, "gemma-3-1b-racon") is not None
    assert find_entry(entries, "ops-model") is not None
    assert find_entry(entries, "ops-model").source == "operator"


def test_catalog_ignores_malformed_operator_file(tmp_path):
    operator = tmp_path / "catalog.json"
    operator.write_text("{not json", encoding="utf-8")
    entries = build_catalog(operator)
    assert find_entry(entries, "gemma-3-1b-racon") is not None
