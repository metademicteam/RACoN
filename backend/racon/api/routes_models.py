"""Models, inference execution, and Handy-style Model Hub routes."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..storage.db import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/models", tags=["Models"])

# Curated catalog of compact models tailored for 16GB RAM / Intel i3 / CPU & iGPU
MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "llama3.2:1b",
        "name": "Llama 3.2 1B (Instruct)",
        "creator": "Meta AI",
        "description": "Ultra-fast state-of-the-art multilingual model. Exceptional speed and reasoning.",
        "size_bytes": 1_300_000_000,
        "size_display": "1.3 GB",
        "parameter_size": "1.2B",
        "accuracy_score": 86,
        "speed_score": 96,
        "recommended_ram_gb": 4.0,
        "format": "GGUF Q4_K_M",
        "tags": ["Fastest", "Multilingual", "Intel i3 Ready", "Recommended"],
        "streaming": True,
    },
    {
        "id": "qwen2.5:0.5b",
        "name": "Qwen 2.5 0.5B (Pocket)",
        "creator": "Alibaba Qwen",
        "description": "Ultra-lightweight on-device model with instant token generation. Lowest RAM usage.",
        "size_bytes": 397_000_000,
        "size_display": "397 MB",
        "parameter_size": "0.5B",
        "accuracy_score": 78,
        "speed_score": 99,
        "recommended_ram_gb": 2.0,
        "format": "GGUF Q4_K_M",
        "tags": ["Pocket Size", "Instant Speed", "Low RAM", "Edge"],
        "streaming": True,
    },
    {
        "id": "qwen2.5:1.5b",
        "name": "Qwen 2.5 1.5B (Balanced)",
        "creator": "Alibaba Qwen",
        "description": "Superior conversational precision, coding capability, and instruction following under 1 GB.",
        "size_bytes": 986_000_000,
        "size_display": "986 MB",
        "parameter_size": "1.5B",
        "accuracy_score": 89,
        "speed_score": 92,
        "recommended_ram_gb": 4.0,
        "format": "GGUF Q4_K_M",
        "tags": ["Balanced", "Math & Code", "High Accuracy"],
        "streaming": True,
    },
    {
        "id": "smollm2:360m",
        "name": "SmolLM2 360M (Instant)",
        "creator": "Hugging Face",
        "description": "Tiny language model designed to run on literally any hardware with virtually zero latency.",
        "size_bytes": 229_000_000,
        "size_display": "229 MB",
        "parameter_size": "0.36B",
        "accuracy_score": 72,
        "speed_score": 100,
        "recommended_ram_gb": 1.5,
        "format": "GGUF Q4_K_M",
        "tags": ["Ultra Light", "Instant", "Zero Lag"],
        "streaming": True,
    },
    {
        "id": "smollm2:1.7b",
        "name": "SmolLM2 1.7B (Instruct)",
        "creator": "Hugging Face",
        "description": "Trained on 11 trillion tokens. Outstanding conversational quality with tiny memory footprint.",
        "size_bytes": 1_050_000_000,
        "size_display": "1.0 GB",
        "parameter_size": "1.7B",
        "accuracy_score": 88,
        "speed_score": 90,
        "recommended_ram_gb": 4.0,
        "format": "GGUF Q4_K_M",
        "tags": ["11T Tokens", "Instruction Following", "Multilingual"],
        "streaming": True,
    },
    {
        "id": "deepseek-r1:1.5b",
        "name": "DeepSeek-R1 Distill 1.5B",
        "creator": "DeepSeek AI",
        "description": "Specialized reasoning model with built-in chain-of-thought distillation for logic and math.",
        "size_bytes": 1_120_000_000,
        "size_display": "1.1 GB",
        "parameter_size": "1.5B",
        "accuracy_score": 92,
        "speed_score": 85,
        "recommended_ram_gb": 4.5,
        "format": "GGUF Q4_K_M",
        "tags": ["Reasoning", "Chain-of-Thought", "Logic", "New"],
        "streaming": True,
    },
    {
        "id": "phi3.5:latest",
        "name": "Phi-3.5 Mini (3.8B)",
        "creator": "Microsoft Research",
        "description": "State-of-the-art small language model rivaling models twice its size on reasoning benchmarks.",
        "size_bytes": 2_200_000_000,
        "size_display": "2.2 GB",
        "parameter_size": "3.8B",
        "accuracy_score": 95,
        "speed_score": 78,
        "recommended_ram_gb": 6.0,
        "format": "GGUF Q4_K_M",
        "tags": ["Highest Accuracy", "Microsoft", "128k Context"],
        "streaming": True,
    },
]

# Track real-time pull status per model ID
_PULL_STATUS: dict[str, dict[str, Any]] = {}

# Inventory cache. Empty until a runtime reports real models; never seeded with invented
# sizes or names.
_CACHED_MODELS: list[dict[str, Any]] = []


class InferenceRequest(BaseModel):
    model_name: str = "qwen2.5:1.5b"
    prompt: str = Field(..., description="Prompt text to generate completions for")
    system_prompt: Optional[str] = "You are a concise AI assistant in the RACoN compute network."
    temperature: float = 0.7
    max_tokens: int = 256
    stream: bool = False


class PullModelRequest(BaseModel):
    model_name: str = Field(..., description="Name of the model to pull (e.g. 'llama3.2:1b')")
    insecure: bool = False


class ImportGGUFRequest(BaseModel):
    url: Optional[str] = None
    file_path: Optional[str] = None
    custom_name: Optional[str] = None


@router.get("/catalog")
async def get_model_catalog():
    """Retrieve the Handy-style model catalog with installation status."""
    installed_models = await list_models()
    installed_names = {m.get("name", "").lower() for m in installed_models}
    installed_filenames = _installed_filenames()

    catalog = []
    for item in MODEL_CATALOG:
        m_id = item["id"].lower()
        # Check if installed (matching 'llama3.2:1b' or 'llama3.2:1b-instruct-q4_k_m')
        is_installed = any(
            m_id in inst or inst.startswith(m_id.split(":")[0])
            for inst in installed_names
        )

        # Include live pull status if active
        pull_info = _PULL_STATUS.get(item["id"], {
            "status": "installed" if is_installed else "idle",
            "percentage": 100 if is_installed else 0,
            "message": "Ready" if is_installed else "Available to Download",
        })

        entry = dict(item)
        entry["is_installed"] = is_installed
        entry["pull_status"] = pull_info
        catalog.append(entry)

    for item in _engine_catalog_items():
        if any(existing["id"] == item["id"] for existing in catalog):
            continue
        installed = item["filename"] in installed_filenames
        pull_info = _PULL_STATUS.get(item["id"], {
            "status": "installed" if installed else "idle",
            "percentage": 100 if installed else 0,
            "message": "Ready" if installed else "Available to download from Hugging Face",
        })
        entry = dict(item)
        entry["is_installed"] = installed
        entry["pull_status"] = pull_info
        catalog.append(entry)

    return {
        "catalog": catalog,
        "installed_count": sum(1 for c in catalog if c["is_installed"]),
        "total_available": len(catalog),
    }


def _installed_filenames() -> set[str]:
    """Model filenames already present on disk, from the canonical model manager."""
    try:
        from racon.services.engine_service import get_engine

        return {model.filename for model in get_engine().models.installed()}
    except Exception as exc:
        logger.debug("Could not enumerate installed GGUF files: %s", exc)
        return set()


def _engine_catalog_items() -> list[dict[str, Any]]:
    """Catalog entries that carry a real download URL (Hugging Face or operator)."""
    from racon.models.catalog import build_catalog

    items: list[dict[str, Any]] = []
    for entry in build_catalog():
        if not entry.download_url:
            continue
        total = entry.total_size_bytes or 0
        items.append({
            "id": entry.id,
            "name": entry.display_name,
            "creator": "Hugging Face" if entry.source == "huggingface" else "RACoN",
            "description": entry.description,
            "size_bytes": total,
            "size_display": f"{total / 1048576:.1f} MB" if total else "unknown size",
            "parameter_size": entry.parameter_size or "unknown",
            "accuracy_score": None,
            "speed_score": None,
            "recommended_ram_gb": round((entry.min_ram_mb or 0) / 1024, 2),
            "format": f"GGUF {entry.quantization}".strip(),
            "tags": ["Hugging Face", "GGUF", entry.parameter_size or "small"],
            "streaming": True,
            "source": entry.source,
            "license": entry.license,
            "repo_id": _repo_from_url(entry.download_url),
            "filename": entry.files[0].filename if entry.files else None,
            "sha256": entry.files[0].sha256 if entry.files else None,
        })
    return items


def _repo_from_url(url: Optional[str]) -> Optional[str]:
    if not url or "huggingface.co/" not in url:
        return None
    tail = url.split("huggingface.co/", 1)[1]
    parts = tail.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return None


@router.get("/recommendations")
def model_recommendations():
    """Recommend models from measured hardware, never from assumed specifications.

    Catalog entries are compared against their declared RAM requirement. Installed GGUF
    files use the real header-derived memory estimate. When a value cannot be measured the
    result is ``fits: null`` with the reason, not a guess.
    """
    from ..hardware import discover_hardware
    from racon.models.estimate import estimate_memory
    from racon.services.engine_service import get_engine

    snap = discover_hardware()
    available = snap.ram_available_mb
    total = snap.ram_total_mb
    vram = snap.gpu.vram_total_mb
    headroom = 0.9

    recommendations: list[dict[str, Any]] = []
    installed_ids: set[str] = set()
    installed_filenames = _installed_filenames()

    try:
        installed_models = get_engine().models.installed()
    except Exception as exc:
        logger.warning("Could not enumerate installed models for recommendations: %s", exc)
        installed_models = []

    for model in installed_models:
        installed_ids.add(model.id)
        estimate = estimate_memory(str(model.path))
        required = estimate.get("total_mb")
        if required is None or available is None:
            fits = None
            reason = "Memory requirement could not be derived from the GGUF header."
        else:
            fits = available * headroom >= float(required)
            reason = (
                f"Estimated {float(required):.0f} MB needed; "
                f"{available:.0f} MB available."
            )
        recommendations.append(
            {
                "id": model.id,
                "name": model.filename,
                "runtime": model.runtime,
                "size_bytes": model.size_bytes,
                "min_ram_mb": required,
                "fits": fits,
                "installed": True,
                "reason": reason,
                "source": "installed",
            }
        )

    for item in MODEL_CATALOG:
        if item["id"] in installed_ids:
            continue
        ram_gb = item.get("recommended_ram_gb")
        required = float(ram_gb) * 1024.0 if ram_gb else None
        if required is None or available is None:
            fits = None
            reason = "This entry declares no RAM requirement."
        else:
            fits = available * headroom >= required
            reason = f"Declared requirement {required:.0f} MB; {available:.0f} MB available."
        recommendations.append(
            {
                "id": item["id"],
                "name": item["name"],
                "runtime": "ollama",
                "size_bytes": item.get("size_bytes"),
                "min_ram_mb": required,
                "fits": fits,
                "installed": False,
                "reason": reason,
                "source": "catalog",
                "downloadable": True,
            }
        )

    for item in _engine_catalog_items():
        if any(existing["id"] == item["id"] for existing in recommendations):
            continue
        installed = item["filename"] in installed_filenames
        required = item.get("recommended_ram_gb") * 1024 if item.get("recommended_ram_gb") else None
        if required is None or available is None:
            fits = None
            reason = "This entry declares no RAM requirement."
        else:
            fits = available * headroom >= required
            reason = f"Declared requirement {required:.0f} MB; {available:.0f} MB available."
        recommendations.append(
            {
                "id": item["id"],
                "name": item["name"],
                "runtime": item.get("format", "GGUF"),
                "size_bytes": item.get("size_bytes"),
                "min_ram_mb": required,
                "fits": fits,
                "installed": installed,
                "reason": reason,
                "source": item.get("source", "huggingface"),
                "downloadable": True,
                "repo_id": item.get("repo_id"),
                "filename": item.get("filename"),
                "sha256": item.get("sha256"),
            }
        )

    def _sort_key(entry: dict[str, Any]):
        fits_rank = 0 if entry["fits"] is True else (1 if entry["fits"] is None else 2)
        installed_rank = 0 if entry["installed"] else 1
        return (fits_rank, installed_rank, -(entry["size_bytes"] or 0))

    recommendations.sort(key=_sort_key)
    recommended_id = next(
        (entry["id"] for entry in recommendations if entry["fits"] and entry["installed"]),
        None,
    )
    if recommended_id is None:
        recommended_id = next(
            (entry["id"] for entry in recommendations if entry["fits"] is True),
            None,
        )

    return {
        "measured": {
            "ram_total_mb": total,
            "ram_available_mb": available,
            "gpu_name": snap.gpu.name,
            "gpu_vram_mb": vram,
            "gpu_telemetry_available": snap.gpu.supported,
        },
        "recommended_id": recommended_id,
        "recommendations": recommendations,
    }


@router.get("")
async def list_models():
    """Retrieve list of available LLMs from local Ollama runtime and cached registry."""
    global _CACHED_MODELS
    ollama_url = settings.ollama_url.rstrip("/")
    models = list(_CACHED_MODELS)

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                discovered = []
                for m in data.get("models", []):
                    discovered.append({
                        "name": m.get("name"),
                        "size_bytes": m.get("size", 0),
                        "format": m.get("details", {}).get("format", "gguf"),
                        "family": m.get("details", {}).get("family", "unknown"),
                        "parameter_size": m.get("details", {}).get("parameter_size", "unknown"),
                        "quantization_level": m.get("details", {}).get("quantization_level", "unknown"),
                        "runtime": "ollama (live)",
                    })
                if discovered:
                    models = discovered
                    _CACHED_MODELS = discovered
    except Exception as e:
        logger.debug("Ollama models query failed: %s", e)

    return models


@router.post("/refresh")
async def refresh_models():
    """Force active query to Ollama service to reload local model inventory."""
    return await list_models()


async def _run_ollama_pull(model_name: str):
    """Background task to pull model via Ollama with progress reporting."""
    ollama_url = settings.ollama_url.rstrip("/")
    _PULL_STATUS[model_name] = {
        "status": "pulling",
        "percentage": 5,
        "completed_bytes": 0,
        "total_bytes": 0,
        "message": f"Contacting Ollama to pull {model_name}...",
        "error": None,
    }

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            # Check if ollama is reachable
            try:
                ping = await client.get(f"{ollama_url}/api/tags", timeout=2.0)
                ollama_online = ping.status_code == 200
            except Exception:
                ollama_online = False

            if ollama_online:
                async with client.stream("POST", f"{ollama_url}/api/pull", json={"name": model_name, "stream": True}) as response:
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            import json
                            chunk = json.loads(line)
                            status_msg = chunk.get("status", "")
                            total = chunk.get("total", 0)
                            completed = chunk.get("completed", 0)
                            pct = round((completed / total * 100), 1) if total > 0 else 10

                            _PULL_STATUS[model_name] = {
                                "status": "downloading" if completed > 0 else "pulling",
                                "percentage": pct,
                                "completed_bytes": completed,
                                "total_bytes": total,
                                "message": f"{status_msg} ({pct}%)",
                                "error": None,
                            }
                        except Exception:
                            pass
            else:
                # No runtime to pull from. Report it instead of animating fake progress.
                _PULL_STATUS[model_name] = {
                    "status": "failed",
                    "percentage": 0,
                    "completed_bytes": 0,
                    "total_bytes": 0,
                    "message": (
                        f"Ollama is not reachable at {ollama_url}. "
                        "Start it with 'ollama serve' or install a GGUF model locally."
                    ),
                    "error": "runtime_unavailable",
                }
                return

        _PULL_STATUS[model_name] = {
            "status": "completed",
            "percentage": 100,
            "message": f"Model {model_name} successfully downloaded and ready for inference!",
            "error": None,
        }

        # Refresh the inventory from the runtime instead of inventing metadata.
        global _CACHED_MODELS
        _CACHED_MODELS.clear()
        try:
            await list_models()
        except Exception as exc:
            logger.debug("Post-pull inventory refresh failed: %s", exc)

    except Exception as e:
        logger.error("Failed to pull model %s: %s", model_name, e)
        _PULL_STATUS[model_name] = {
            "status": "failed",
            "percentage": 0,
            "message": f"Download failed: {str(e)}",
            "error": str(e),
        }


async def _run_gguf_download(entry) -> None:
    """Download a catalog entry through the canonical model manager."""
    model_id = entry.id
    expected_total = entry.total_size_bytes or 0
    _PULL_STATUS[model_id] = {
        "status": "downloading",
        "percentage": 0,
        "completed_bytes": 0,
        "total_bytes": expected_total,
        "message": f"Downloading {entry.display_name}…",
        "error": None,
    }

    def progress(downloaded: int, total: int) -> None:
        total_bytes = total or expected_total
        pct = round(downloaded / total_bytes * 100, 1) if total_bytes else 0.0
        _PULL_STATUS[model_id] = {
            "status": "downloading",
            "percentage": min(pct, 99.9),
            "completed_bytes": downloaded,
            "total_bytes": total_bytes,
            "message": f"Downloading… {pct:.1f}%",
            "error": None,
        }

    try:
        from racon.services.engine_service import get_engine

        result = await get_engine().models.download(entry, progress=progress)
        _PULL_STATUS[model_id] = {
            "status": "completed",
            "percentage": 100,
            "completed_bytes": result.model.size_bytes,
            "total_bytes": result.model.size_bytes,
            "message": "Downloaded, SHA-256 verified and installed.",
            "error": None,
            "verified": result.verified,
            "path": str(result.model.path),
        }
        logger.info("Installed %s from %s", model_id, entry.download_url)
    except Exception as exc:
        logger.error("GGUF download failed for %s: %s", model_id, exc)
        _PULL_STATUS[model_id] = {
            "status": "failed",
            "percentage": 0,
            "completed_bytes": 0,
            "total_bytes": expected_total,
            "message": f"Download failed: {exc}",
            "error": str(exc),
        }


@router.post("/pull")
async def pull_model(req: PullModelRequest, background_tasks: BackgroundTasks):
    """Trigger a real model download.

    Entries with an HTTPS artifact (Hugging Face or operator catalog) go through the model
    manager, which verifies SHA-256. Ollama tags are delegated to the local daemon.
    """
    model_name = req.model_name.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name is required")

    from racon.models.catalog import build_catalog, find_entry

    entry = find_entry(build_catalog(), model_name)
    if entry is not None and entry.download_url:
        if not entry.files or not entry.files[0].sha256:
            raise HTTPException(
                status_code=422,
                detail=f"'{model_name}' has no SHA-256; refusing an unverified download.",
            )
        background_tasks.add_task(_run_gguf_download, entry)
        return {
            "status": "initiated",
            "model_name": model_name,
            "message": f"Downloading {entry.display_name}",
            "source": entry.source,
        }

    if entry is not None and not entry.download_url and entry.source == "builtin":
        raise HTTPException(
            status_code=400,
            detail=f"'{model_name}' is a local-only model. Import the GGUF file instead.",
        )

    background_tasks.add_task(_run_ollama_pull, model_name)
    return {
        "status": "initiated",
        "model_name": model_name,
        "message": f"Model download started for {model_name}",
    }


class HfInstallRequest(BaseModel):
    repo_id: str = Field(..., min_length=3, max_length=200)
    filename: str = Field(..., min_length=5, max_length=512)
    revision: str = Field(default="main", max_length=64)
    model_id: Optional[str] = Field(default=None, max_length=128)


@router.get("/hf/files")
async def hf_list_files(
    repo_id: str = Query(..., description="Hugging Face repository, e.g. owner/name"),
    revision: str = Query("main", max_length=64),
):
    """List GGUF artifacts in a public or token-authorized Hugging Face repository."""
    from racon.models.huggingface import HuggingFaceClient, HuggingFaceError

    try:
        files = await HuggingFaceClient().list_gguf_files(repo_id, revision)
    except HuggingFaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"repo_id": repo_id, "revision": revision, "files": [f.to_dict() for f in files]}


@router.post("/hf/install")
async def hf_install(req: HfInstallRequest, background_tasks: BackgroundTasks):
    """Install a specific GGUF file from Hugging Face after verifying its Hub SHA-256."""
    from racon.models.huggingface import HuggingFaceClient, HuggingFaceError, build_entry

    try:
        files = await HuggingFaceClient().list_gguf_files(req.repo_id, req.revision)
    except HuggingFaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    match = next(
        (item for item in files if item.path == req.filename or item.filename == req.filename),
        None,
    )
    if match is None:
        raise HTTPException(status_code=404, detail=f"'{req.filename}' is not a GGUF file in this repository")
    if not match.sha256:
        raise HTTPException(
            status_code=422,
            detail="Hugging Face did not report a SHA-256 for this file; refusing an unverified download.",
        )

    entry = build_entry(match, model_id=req.model_id)
    background_tasks.add_task(_run_gguf_download, entry)
    return {
        "status": "initiated",
        "model_id": entry.id,
        "repo_id": match.repo_id,
        "filename": match.filename,
        "size_bytes": match.size_bytes,
        "sha256": match.sha256,
    }


@router.get("/hf/whoami")
async def hf_whoami():
    """Report whether a Hugging Face token is configured, without revealing it."""
    from racon.models.huggingface import HuggingFaceClient

    return await HuggingFaceClient().whoami()


@router.get("/pull/status")
async def get_pull_status(model_name: str = Query(..., description="Model ID to check")):
    """Get active download progress for a model."""
    status_info = _PULL_STATUS.get(model_name, {
        "status": "idle",
        "percentage": 0,
        "message": "No active download for this model",
        "error": None,
    })
    return status_info


@router.post("/infer")
async def run_inference(req: InferenceRequest):
    """Execute inference through the real local engine.

    Returns a structured error when no runtime can serve the model. No simulated reply is
    ever produced.
    """
    from racon.runtime.types import ChatMessage, RaconRuntimeError
    from racon.services.engine_service import get_engine

    start_time = time.monotonic()
    db = get_database()

    messages = []
    if req.system_prompt:
        messages.append(ChatMessage(role="system", content=req.system_prompt))
    messages.append(ChatMessage(role="user", content=req.prompt))

    try:
        result = await get_engine().chat(
            messages,
            model=req.model_name,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
    except RaconRuntimeError as exc:
        db.log_audit(
            "INFERENCE_FAILED",
            f"Inference on {req.model_name} failed: {exc.code}",
            level="WARN",
        )
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    elapsed_ms = (time.monotonic() - start_time) * 1000
    db.log_audit(
        "INFERENCE_LIVE",
        f"Live inference on {req.model_name} completed ({result.usage.completion_tokens} tokens)",
    )

    return {
        "model": req.model_name,
        "prompt": req.prompt,
        "response": result.text,
        "input_tokens": result.usage.prompt_tokens,
        "output_tokens": result.usage.completion_tokens,
        "duration_ms": round(elapsed_ms, 1),
        "tokens_per_second": result.generation_tokens_per_second,
        "simulated": False,
    }
