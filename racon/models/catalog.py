"""Model catalog: what RACoN knows how to install and run.

A catalog entry is metadata only — it never contains executable payloads. Downloads are
HTTPS-only and every artifact may carry a SHA-256 that is verified before use.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: The GGUF that ships alongside the repository for local development and demos.
#: Centralised here so no UI module hardcodes a filename any more.
LOCAL_GEMMA_FILENAME = "Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_Q4_k_m.gguf"


@dataclass(frozen=True)
class ModelFile:
    """One downloadable artifact belonging to a model."""

    filename: str
    url: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "url": self.url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass
class CatalogEntry:
    """A model that can be installed into a local runtime."""

    id: str
    display_name: str
    runtime: str
    files: list[ModelFile] = field(default_factory=list)
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    context_length: int = 4096
    min_ram_mb: Optional[float] = None
    license: str = ""
    description: str = ""
    source: str = "builtin"
    ollama_tag: Optional[str] = None

    @property
    def download_url(self) -> Optional[str]:
        for item in self.files:
            if item.url:
                return item.url
        return None

    @property
    def total_size_bytes(self) -> Optional[int]:
        sizes = [f.size_bytes for f in self.files if f.size_bytes]
        return sum(sizes) if sizes else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "runtime": self.runtime,
            "files": [f.to_dict() for f in self.files],
            "family": self.family,
            "parameter_size": self.parameter_size,
            "quantization": self.quantization,
            "context_length": self.context_length,
            "min_ram_mb": self.min_ram_mb,
            "license": self.license,
            "description": self.description,
            "source": self.source,
            "ollama_tag": self.ollama_tag,
            "total_size_bytes": self.total_size_bytes,
        }


BUILTIN_MODELS: list[CatalogEntry] = [
    CatalogEntry(
        id="gemma-3-1b-racon",
        display_name="Gemma 3 1B (RACoN build)",
        runtime="llama_cpp",
        files=[ModelFile(filename=LOCAL_GEMMA_FILENAME)],
        family="gemma3",
        parameter_size="1B",
        quantization="Q4_K_M",
        context_length=32768,
        min_ram_mb=1100.0,
        license="Gemma Terms of Use",
        description=(
            "Small quantised instruct model bundled with the repository. "
            "Serves as the CPU-only baseline for local inference."
        ),
        source="builtin",
    ),
    CatalogEntry(
        id="SmolLM2-360M-Instruct-Q4_K_M",
        display_name="SmolLM2 360M Instruct (Q4_K_M)",
        runtime="llama_cpp",
        files=[
            ModelFile(
                filename="SmolLM2-360M-Instruct-Q4_K_M.gguf",
                url=(
                    "https://huggingface.co/bartowski/SmolLM2-360M-Instruct-GGUF/"
                    "resolve/main/SmolLM2-360M-Instruct-Q4_K_M.gguf"
                ),
                sha256="2fa3f013dcdd7b99f9b237717fa0b12d75bbb89984cc1274be1471a465bac9c2",
                size_bytes=270590880,
            )
        ],
        family="smollm2",
        parameter_size="360M",
        quantization="Q4_K_M",
        context_length=4096,
        min_ram_mb=768.0,
        license="Apache-2.0",
        description="Smallest Hugging Face option. Runs on low-memory laptops and CPUs.",
        source="huggingface",
        ollama_tag="smollm2:360m",
    ),
    CatalogEntry(
        id="Qwen2.5-0.5B-Instruct-Q4_K_M",
        display_name="Qwen 2.5 0.5B Instruct (Q4_K_M)",
        runtime="llama_cpp",
        files=[
            ModelFile(
                filename="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
                url=(
                    "https://huggingface.co/bartowski/Qwen2.5-0.5B-Instruct-GGUF/"
                    "resolve/main/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
                ),
                sha256="6eb923e7d26e9cea28811e1a8e852009b21242fb157b26149d3b188f3a8c8653",
                size_bytes=397808192,
            )
        ],
        family="qwen2",
        parameter_size="0.5B",
        quantization="Q4_K_M",
        context_length=8192,
        min_ram_mb=1024.0,
        license="See the model card on Hugging Face",
        description="Balanced small model for CPU inference with good instruction following.",
        source="huggingface",
        ollama_tag="qwen2.5:0.5b",
    ),
    CatalogEntry(
        id="Llama-3.2-1B-Instruct-Q4_K_M",
        display_name="Llama 3.2 1B Instruct (Q4_K_M)",
        runtime="llama_cpp",
        files=[
            ModelFile(
                filename="Llama-3.2-1B-Instruct-Q4_K_M.gguf",
                url=(
                    "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/"
                    "resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf"
                ),
                sha256="6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83",
                size_bytes=807694464,
            )
        ],
        family="llama",
        parameter_size="1B",
        quantization="Q4_K_M",
        context_length=8192,
        min_ram_mb=1536.0,
        license="Llama 3.2 Community License",
        description="Higher-quality small model for machines with at least 2 GB free RAM.",
        source="huggingface",
        ollama_tag="llama3.2:1b",
    ),
]

#: Ollama models the model hub can pull. Tags are the published Ollama names, which the
#: daemon resolves itself — RACoN never downloads a blob directly for these.
BUILTIN_OLLAMA_TAGS: list[tuple[str, str]] = [
    ("qwen2.5:0.5b", "Qwen 2.5 0.5B"),
    ("qwen2.5:1.5b", "Qwen 2.5 1.5B"),
    ("llama3.2:1b", "Llama 3.2 1B"),
    ("smollm2:360m", "SmolLM2 360M"),
]


def ollama_entries() -> list[CatalogEntry]:
    """Catalog entries backed by an Ollama pull."""
    entries: list[CatalogEntry] = []
    for tag, name in BUILTIN_OLLAMA_TAGS:
        entries.append(
            CatalogEntry(
                id=f"ollama:{tag}",
                display_name=name,
                runtime="ollama",
                family="",
                parameter_size="",
                quantization="",
                description=f"Pull '{tag}' through the local Ollama daemon.",
                source="ollama",
                ollama_tag=tag,
            )
        )
    return entries


def load_operator_catalog(path: Path) -> list[CatalogEntry]:
    """Load an operator-supplied JSON catalog.

    Format is a JSON array of entries with the same field names as :class:`CatalogEntry`.
    Invalid entries are skipped with a warning; a broken file never blocks start-up.
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring invalid model catalog %s: %s", path, exc)
        return []

    if isinstance(raw, dict):
        raw = raw.get("models", [])

    entries: list[CatalogEntry] = []
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("id") or not item.get("runtime"):
            logger.warning("Skipping malformed catalog entry: %r", item)
            continue
        files = [
            ModelFile(
                filename=str(f.get("filename", "")),
                url=f.get("url"),
                sha256=f.get("sha256"),
                size_bytes=f.get("size_bytes"),
            )
            for f in item.get("files", [])
            if isinstance(f, dict) and f.get("filename")
        ]
        entries.append(
            CatalogEntry(
                id=str(item["id"]),
                display_name=str(item.get("display_name") or item["id"]),
                runtime=str(item["runtime"]),
                files=files,
                family=str(item.get("family", "")),
                parameter_size=str(item.get("parameter_size", "")),
                quantization=str(item.get("quantization", "")),
                context_length=int(item.get("context_length", 4096) or 4096),
                min_ram_mb=item.get("min_ram_mb"),
                license=str(item.get("license", "")),
                description=str(item.get("description", "")),
                source="operator",
                ollama_tag=item.get("ollama_tag"),
            )
        )
    return entries


def build_catalog(operator_path: Optional[Path] = None) -> list[CatalogEntry]:
    """Return the merged catalog: built-ins, Ollama tags, then operator additions."""
    entries = list(BUILTIN_MODELS) + ollama_entries()
    if operator_path is not None:
        entries.extend(load_operator_catalog(operator_path))
    return entries


def find_entry(entries: list[CatalogEntry], model_id: str) -> Optional[CatalogEntry]:
    """Look an entry up by id, or by Ollama tag as a convenience.

    A tag lookup prefers the entry that is actually pulled through Ollama: GGUF catalog
    entries carry the equivalent tag only so the runtime can translate a filename, and
    returning one of those here would make ``/models/pull`` download a GGUF instead of
    pulling the tag.
    """
    for entry in entries:
        if entry.id == model_id:
            return entry
    for entry in entries:
        if entry.ollama_tag == model_id and entry.runtime == "ollama":
            return entry
    for entry in entries:
        if entry.ollama_tag == model_id:
            return entry
    return None


def ollama_tag_for(model_id: str) -> Optional[str]:
    """Return the Ollama tag equivalent to a GGUF/catalog model id, if one is known.

    RACoN model ids come from GGUF filenames (``Qwen2.5-0.5B-Instruct-Q4_K_M``) while
    Ollama only accepts published tags (``qwen2.5:0.5b``). Passing the filename straight
    through produced honest but confusing ``404 model_not_found`` errors, so callers that
    are about to talk to Ollama translate known ids first.
    """
    text = (model_id or "").strip()
    if not text:
        return None
    prefix = "ollama:"
    if text.lower().startswith(prefix):
        return text[len(prefix):]
    lowered = text.lower()
    for entry in BUILTIN_MODELS:
        if not entry.ollama_tag:
            continue
        candidates = {entry.id.lower()}
        candidates.update(item.filename.lower() for item in entry.files)
        candidates.update(
            item.filename.rsplit(".", 1)[0].lower() for item in entry.files
        )
        if lowered in candidates:
            return entry.ollama_tag
    return None
