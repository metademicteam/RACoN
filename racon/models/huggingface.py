"""Hugging Face Hub client for discovering and installing GGUF models.

Only HTTPS downloads from `huggingface.co` are permitted. When the Hub reports an LFS
object id (SHA-256), it becomes the integrity check for the download. A token is optional
and is read from the environment at call time, never persisted by this module.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

from racon.models.catalog import CatalogEntry, ModelFile

DEFAULT_BASE_URL = "https://huggingface.co"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


class HuggingFaceError(RuntimeError):
    """The Hub rejected the request or returned unusable data."""


@dataclass(frozen=True)
class HfFile:
    repo_id: str
    path: str
    size_bytes: Optional[int]
    sha256: Optional[str]
    download_url: str

    @property
    def filename(self) -> str:
        return Path(self.path).name

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "path": self.path,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "download_url": self.download_url,
        }


def huggingface_token() -> Optional[str]:
    """Read a token from the environment. Never stored in configuration files."""
    for key in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    try:
        from racon.settings import get_settings

        token = get_settings().hf_token
        return token.strip() if token else None
    except Exception:
        return None


def validate_repo_id(repo_id: str) -> str:
    cleaned = (repo_id or "").strip().strip("/")
    if not REPO_PATTERN.match(cleaned):
        raise HuggingFaceError(
            "Repository id must look like 'owner/name' and contain only letters, digits, '.', '-' or '_'."
        )
    return cleaned


def validate_revision(revision: str) -> str:
    cleaned = (revision or "main").strip()
    if not REVISION_PATTERN.match(cleaned):
        raise HuggingFaceError("Revision contains unsupported characters.")
    return cleaned


def slug_for(repo_id: str, path: str) -> str:
    stem = Path(path).stem
    slug = SLUG_PATTERN.sub("-", f"hf-{repo_id}-{stem}".lower()).strip("-")
    return slug[:96] or "hf-model"


def build_entry(
    file: HfFile,
    *,
    model_id: Optional[str] = None,
    display_name: Optional[str] = None,
    family: str = "",
    parameter_size: str = "",
    quantization: str = "",
    context_length: int = 4096,
    min_ram_mb: Optional[float] = None,
    license: str = "See the model card on Hugging Face",
    description: str = "",
) -> CatalogEntry:
    """Turn a Hub file into a catalog entry with a verifiable download."""
    return CatalogEntry(
        id=model_id or Path(file.path).stem[:96],
        display_name=display_name or f"{file.repo_id} — {file.filename}",
        runtime="llama_cpp",
        files=[
            ModelFile(
                filename=file.filename,
                url=file.download_url,
                sha256=file.sha256,
                size_bytes=file.size_bytes,
            )
        ],
        family=family,
        parameter_size=parameter_size,
        quantization=quantization,
        context_length=context_length,
        min_ram_mb=min_ram_mb,
        license=license,
        description=description or f"Downloaded from https://huggingface.co/{file.repo_id}",
        source="huggingface",
    )


class HuggingFaceClient:
    """Async client for the public Hub API."""

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        client_factory=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token = token if token is not None else huggingface_token()
        self._client_factory = client_factory or self._default_client

    def _default_client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json", "User-Agent": "RACoN/1.0"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(timeout=self.timeout, headers=headers)

    async def list_gguf_files(self, repo_id: str, revision: str = "main") -> list[HfFile]:
        repo = validate_repo_id(repo_id)
        rev = validate_revision(revision)
        url = f"{self.base_url}/api/models/{repo}/tree/{rev}?recursive=true"

        try:
            async with self._client_factory() as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise HuggingFaceError(f"Could not reach Hugging Face: {exc}") from exc

        if response.status_code == 401:
            raise HuggingFaceError("This repository requires a Hugging Face token.")
        if response.status_code == 403:
            raise HuggingFaceError("Access to this repository was denied (gated or private).")
        if response.status_code == 404:
            raise HuggingFaceError(f"Repository or revision not found: {repo}@{rev}")
        if response.status_code >= 400:
            raise HuggingFaceError(f"Hugging Face returned HTTP {response.status_code}.")

        try:
            entries = response.json()
        except ValueError as exc:
            raise HuggingFaceError("Hugging Face returned a non-JSON response.") from exc

        files: list[HfFile] = []
        for item in entries if isinstance(entries, list) else []:
            if not isinstance(item, dict) or item.get("type") != "file":
                continue
            path = str(item.get("path") or "")
            if not path.lower().endswith(".gguf"):
                continue
            size = item.get("size")
            lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
            sha = lfs.get("oid")
            if not isinstance(sha, str) or not SHA256_PATTERN.match(sha):
                sha = None
            files.append(
                HfFile(
                    repo_id=repo,
                    path=path,
                    size_bytes=int(size) if isinstance(size, (int, float)) else None,
                    sha256=sha,
                    download_url=f"{self.base_url}/{repo}/resolve/{rev}/{quote(path, safe='/')}",
                )
            )

        files.sort(key=lambda f: (f.size_bytes if f.size_bytes is not None else 1 << 62, f.path))
        return files

    async def whoami(self) -> dict[str, Any]:
        if not self._token:
            return {"authenticated": False, "reason": "No HF_TOKEN configured."}
        try:
            async with self._client_factory() as client:
                response = await client.get(f"{self.base_url}/api/whoami-v2")
        except httpx.HTTPError as exc:
            return {"authenticated": False, "reason": f"Network error: {exc}"}
        if response.status_code != 200:
            return {"authenticated": False, "reason": f"HTTP {response.status_code}"}
        try:
            data = response.json()
        except ValueError:
            return {"authenticated": False, "reason": "Invalid response."}
        return {
            "authenticated": True,
            "name": data.get("name"),
            "type": data.get("type"),
        }
