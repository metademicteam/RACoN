"""Local model storage: discovery, verified installation, and deletion.

Security rules enforced here:
* remote artifacts must be HTTPS;
* a remote artifact without a SHA-256 is refused unless the caller explicitly opts in;
* bytes land in a ``.part`` file and are renamed only after the digest matches;
* a free-space check runs before any bytes are written.

Nothing in this module executes a model. GGUF files are data; the runtime that loads them
is chosen separately by the scheduler.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

import httpx

from racon.models.catalog import CatalogEntry, build_catalog, find_entry
from racon.models.estimate import estimate_memory
from racon.runtime.distributed_gguf import compute_file_sha256, inspect_gguf_metadata

logger = logging.getLogger(__name__)

GGUF_SUFFIXES = (".gguf",)
CHUNK_SIZE = 1024 * 512
DEFAULT_MIN_FREE_MB = 1024.0

ProgressCallback = Callable[[int, Optional[int]], None]


class ModelStorageError(Exception):
    """Raised when a model cannot be stored safely."""

    code = "model_storage_error"

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InsufficientDiskSpaceError(ModelStorageError):
    code = "insufficient_disk_space"


class ModelVerificationError(ModelStorageError):
    code = "model_verification_failed"


class UntrustedSourceError(ModelStorageError):
    code = "untrusted_model_source"


@dataclass
class InstalledModel:
    """A model file that exists on this machine."""

    id: str
    path: Path
    size_bytes: int
    runtime: str = "llama_cpp"
    family: str = ""
    context_length: Optional[int] = None
    quantization: str = ""
    sha256: Optional[str] = None
    source: str = "local"

    @property
    def filename(self) -> str:
        return self.path.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": str(self.path),
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "size_mb": round(self.size_bytes / (1024 * 1024), 1),
            "runtime": self.runtime,
            "family": self.family,
            "context_length": self.context_length,
            "quantization": self.quantization,
            "sha256": self.sha256,
            "source": self.source,
        }


@dataclass
class InstallResult:
    model: InstalledModel
    verified: bool
    downloaded_bytes: int = 0
    resumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "verified": self.verified,
            "downloaded_bytes": self.downloaded_bytes,
            "resumed": self.resumed,
        }


def _is_https(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


class ModelManager:
    """Owns the local model directory."""

    def __init__(
        self,
        models_dir: Path,
        *,
        catalog_path: Optional[Path] = None,
        extra_search_dirs: Optional[Iterable[Path]] = None,
        min_free_mb: float = DEFAULT_MIN_FREE_MB,
    ) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = build_catalog(catalog_path)
        self.extra_search_dirs = [Path(p) for p in (extra_search_dirs or [])]
        self.min_free_mb = min_free_mb
        # A scan walks every configured root and parses each GGUF header. The API asks
        # for this inventory several times per inference request, so a short-lived cache
        # keeps the event loop free without hiding newly copied files for long.
        self._scan_ttl = 2.0
        self._scan_cache: Optional[tuple[float, list[InstalledModel]]] = None
        self._scan_lock = threading.Lock()

    # -- catalog -----------------------------------------------------------

    def catalog_entries(self) -> list[CatalogEntry]:
        return list(self.catalog)

    def entry(self, model_id: str) -> Optional[CatalogEntry]:
        return find_entry(self.catalog, model_id)

    # -- discovery ---------------------------------------------------------

    def _search_roots(self) -> list[Path]:
        roots = [self.models_dir]
        env_dir = os.environ.get("RACON_MODEL_DIR")
        if env_dir:
            roots.append(Path(env_dir))
        roots.extend(self.extra_search_dirs)
        # Repository root when running from a source checkout.
        repo_root = Path(__file__).resolve().parents[2]
        roots.extend([repo_root, Path.cwd()])
        seen: list[Path] = []
        for root in roots:
            try:
                resolved = root.resolve()
            except OSError:
                continue
            if resolved.is_dir() and resolved not in seen:
                seen.append(resolved)
        return seen

    def _scan(self) -> list[InstalledModel]:
        """Return the installed-model inventory, reusing a recent scan."""
        now = time.monotonic()
        cached = self._scan_cache
        if cached is not None and (now - cached[0]) < self._scan_ttl:
            return list(cached[1])
        with self._scan_lock:
            cached = self._scan_cache
            now = time.monotonic()
            if cached is not None and (now - cached[0]) < self._scan_ttl:
                return list(cached[1])
            models = self._scan_uncached()
            self._scan_cache = (now, models)
            return list(models)

    def invalidate_cache(self) -> None:
        """Drop the cached inventory after an install, import, or delete."""
        with self._scan_lock:
            self._scan_cache = None

    def _scan_uncached(self) -> list[InstalledModel]:
        found: dict[str, InstalledModel] = {}
        for root in self._search_roots():
            if not root.exists():
                continue
            try:
                candidates = list(root.glob("*.gguf"))
            except OSError:
                continue
            for path in candidates:
                if not path.is_file():
                    continue
                key = path.name
                if key in found:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                model_id = path.stem
                family = ""
                ctx: Optional[int] = None
                try:
                    meta = inspect_gguf_metadata(str(path))
                    family = str(meta.get("architecture") or "")
                    ctx = int(meta.get("context_length") or 0) or None
                except Exception as exc:  # metadata is best effort
                    logger.debug("Metadata read failed for %s: %s", path, exc)
                found[key] = InstalledModel(
                    id=model_id,
                    path=path.resolve(),
                    size_bytes=stat.st_size,
                    family=family,
                    context_length=ctx,
                    source="installed" if self.models_dir in path.resolve().parents else "discovered",
                )
        return sorted(found.values(), key=lambda m: m.id.lower())

    def installed(self) -> list[InstalledModel]:
        """Return every GGUF this machine can serve."""
        return self._scan()

    def find_installed(self, model_id: str) -> Optional[InstalledModel]:
        target = Path(model_id)
        if target.is_file() and target.suffix.lower() in GGUF_SUFFIXES:
            return InstalledModel(
                id=target.stem,
                path=target.resolve(),
                size_bytes=target.stat().st_size,
            )
        for model in self._scan():
            if model.id == model_id or model.filename == model_id:
                return model
        return None

    # -- disk --------------------------------------------------------------

    def disk_free_mb(self) -> float:
        usage = shutil.disk_usage(self.models_dir)
        return usage.free / (1024 * 1024)

    def _require_space(self, needed_bytes: int) -> None:
        free_mb = self.disk_free_mb()
        needed_mb = needed_bytes / (1024 * 1024)
        if free_mb < needed_mb + self.min_free_mb:
            raise InsufficientDiskSpaceError(
                f"Not enough disk space: need {needed_mb:.0f} MB plus "
                f"{self.min_free_mb:.0f} MB headroom, {free_mb:.0f} MB free.",
                {"free_mb": round(free_mb, 1), "needed_mb": round(needed_mb, 1)},
            )

    # -- install -----------------------------------------------------------

    def install_from_path(
        self,
        source: str | Path,
        *,
        model_id: Optional[str] = None,
        copy: bool = True,
        expected_sha256: Optional[str] = None,
    ) -> InstallResult:
        """Import an existing GGUF file into the managed model directory."""
        src = Path(source)
        if not src.is_file():
            raise ModelStorageError(f"Model file not found: {src}")
        if src.suffix.lower() not in GGUF_SUFFIXES:
            raise ModelStorageError(f"Not a GGUF file: {src.name}")

        size = src.stat().st_size
        self._require_space(size if copy else 0)

        actual_sha = None
        verified = False
        if expected_sha256:
            actual_sha = compute_file_sha256(str(src))
            if actual_sha.lower() != expected_sha256.lower():
                raise ModelVerificationError(
                    "SHA-256 mismatch for imported model",
                    {"expected": expected_sha256, "actual": actual_sha},
                )
            verified = True

        dest = self.models_dir / src.name
        if src.resolve() != dest.resolve():
            if dest.exists():
                raise ModelStorageError(f"Model already installed: {dest.name}")
            if copy:
                shutil.copy2(src, dest)
            else:
                shutil.move(str(src), str(dest))
        else:
            dest = src

        meta = inspect_gguf_metadata(str(dest))
        self.invalidate_cache()
        return InstallResult(
            model=InstalledModel(
                id=model_id or dest.stem,
                path=dest.resolve(),
                size_bytes=dest.stat().st_size,
                family=str(meta.get("architecture") or ""),
                context_length=int(meta.get("context_length") or 0) or None,
                sha256=actual_sha,
                source="imported",
            ),
            verified=verified,
        )

    async def download(
        self,
        entry: CatalogEntry,
        *,
        progress: Optional[ProgressCallback] = None,
        allow_unverified: bool = False,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 60.0,
    ) -> InstallResult:
        """Download and verify a catalog entry.

        Resumes a partial ``.part`` file when the server honours a Range request.
        """
        if not entry.files:
            raise ModelStorageError(f"Catalog entry '{entry.id}' has no files")
        target = entry.files[0]
        if not target.url:
            raise ModelStorageError(
                f"'{entry.id}' is local-only and has no download URL",
                {"model": entry.id},
            )
        if not _is_https(target.url):
            raise UntrustedSourceError(
                "Refusing to download a model over a non-HTTPS URL",
                {"url": target.url},
            )
        if not target.sha256 and not allow_unverified:
            raise UntrustedSourceError(
                "Refusing to install an unverified model. Provide a SHA-256 or import it manually.",
                {"model": entry.id},
            )

        expected_size = target.size_bytes
        if expected_size:
            self._require_space(expected_size)

        dest = self.models_dir / target.filename
        part = dest.with_suffix(dest.suffix + ".part")
        if dest.exists():
            existing_sha = compute_file_sha256(str(dest))
            if target.sha256 and existing_sha.lower() == target.sha256.lower():
                self.invalidate_cache()
                return InstallResult(
                    model=InstalledModel(
                        id=entry.id,
                        path=dest.resolve(),
                        size_bytes=dest.stat().st_size,
                        family=entry.family,
                        context_length=entry.context_length,
                        quantization=entry.quantization,
                        sha256=existing_sha,
                        source="installed",
                    ),
                    verified=True,
                )
            raise ModelStorageError(
                f"A different file already exists at {dest.name}",
                {"path": str(dest)},
            )

        owns_client = client is None
        http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
        )

        try:
            # Hugging Face LFS transfers are large; a single dropped connection must not
            # throw away the bytes already on disk. Each attempt resumes from the current
            # ``.part`` size using a Range request.
            for attempt in range(3):
                downloaded = part.stat().st_size if part.exists() else 0
                resumed = downloaded > 0
                headers = {
                    "Accept": "application/octet-stream",
                    "User-Agent": "RACoN/1.1",
                }
                if resumed:
                    headers["Range"] = f"bytes={downloaded}-"
                try:
                    mode = "ab" if resumed else "wb"
                    async with http.stream(
                        "GET", target.url, headers=headers, follow_redirects=True
                    ) as resp:
                        if resp.status_code == 416 and resumed:
                            # The partial file is already complete; verify below.
                            logger.info(
                                "Resume requested beyond EOF for %s; verifying existing bytes",
                                part.name,
                            )
                        else:
                            if resp.status_code not in (200, 206):
                                raise ModelStorageError(
                                    f"Download failed with HTTP {resp.status_code}",
                                    {"url": target.url, "status_code": resp.status_code},
                                )
                            total_header = resp.headers.get("Content-Length")
                            total = (downloaded + int(total_header)) if total_header else expected_size
                            if expected_size:
                                self._require_space(max(0, expected_size - downloaded))
                            if resp.status_code == 200 and resumed:
                                # Server ignored the Range request; start over cleanly.
                                downloaded = 0
                                mode = "wb"
                            with open(part, mode) as handle:
                                async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                                    if not chunk:
                                        continue
                                    handle.write(chunk)
                                    downloaded += len(chunk)
                                    if progress:
                                        progress(downloaded, total)
                    break
                except (httpx.TransportError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                    if attempt >= 2:
                        raise
                    logger.warning(
                        "Download attempt %d for %s failed (%s); resuming",
                        attempt + 1,
                        target.filename,
                        exc,
                    )
                    if progress:
                        progress(
                            part.stat().st_size if part.exists() else 0,
                            expected_size,
                        )
                    await asyncio.sleep(1.5 * (attempt + 1))
        except httpx.HTTPError as exc:
            raise ModelStorageError(
                f"Download failed: {exc}", {"url": target.url}
            ) from exc
        finally:
            if owns_client:
                await http.aclose()

        actual_size = part.stat().st_size if part.exists() else 0
        if expected_size and actual_size != expected_size:
            part.unlink(missing_ok=True)
            raise ModelStorageError(
                "Downloaded file size does not match the catalog",
                {"expected_bytes": expected_size, "actual_bytes": actual_size},
            )

        actual_sha = await asyncio.to_thread(compute_file_sha256, str(part))
        if target.sha256 and actual_sha.lower() != target.sha256.lower():
            part.unlink(missing_ok=True)
            raise ModelVerificationError(
                "SHA-256 verification failed; the partial download was discarded",
                {"expected": target.sha256, "actual": actual_sha},
            )

        part.replace(dest)
        meta = inspect_gguf_metadata(str(dest))
        if meta.get("is_valid_gguf") is False:
            dest.unlink(missing_ok=True)
            raise ModelVerificationError("Downloaded file is not a valid GGUF", {"path": str(dest)})

        self.invalidate_cache()
        return InstallResult(
            model=InstalledModel(
                id=entry.id,
                path=dest.resolve(),
                size_bytes=dest.stat().st_size,
                family=str(meta.get("architecture") or entry.family),
                context_length=int(meta.get("context_length") or 0) or None,
                quantization=entry.quantization,
                sha256=actual_sha,
                source="downloaded",
            ),
            verified=bool(target.sha256),
            downloaded_bytes=downloaded,
            resumed=resumed,
        )

    # -- removal -----------------------------------------------------------

    def delete(self, model_id: str) -> bool:
        """Delete a model that lives inside the managed directory.

        Files discovered elsewhere (an operator's own folder) are never deleted.
        """
        model = self.find_installed(model_id)
        if model is None:
            return False
        resolved = model.path.resolve()
        if self.models_dir.resolve() not in resolved.parents:
            raise ModelStorageError(
                "Refusing to delete a model outside the managed directory",
                {"path": str(resolved)},
            )
        resolved.unlink(missing_ok=True)
        self.invalidate_cache()
        return True

    # -- estimation --------------------------------------------------------

    def estimate(self, model_id: str, context_size: int = 4096, kv_cache_type: str = "f16") -> dict[str, Any]:
        model = self.find_installed(model_id)
        if model is None:
            raise ModelStorageError(f"Model '{model_id}' is not installed", {"model": model_id})
        return estimate_memory(model.path, context_size, kv_cache_type)
