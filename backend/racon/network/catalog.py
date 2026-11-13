"""Model inventory exposed by the RACoN network gateway."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

VIRTUAL_MODEL_IDS = ("racon", "racon-single", "racon-task", "racon-agent")
ONLINE_WINDOW_SECONDS = 30.0


def is_advertisable_model(name: str) -> bool:
    """Only chat-capable local/worker models belong in the OpenAI list."""
    lowered = (name or "").strip().lower()
    if not lowered:
        return False
    if lowered.endswith(":cloud") or "-cloud" in lowered:
        return False
    if "embed" in lowered or "rerank" in lowered or "mmproj" in lowered:
        return False
    return True


def online_workers() -> list[Any]:
    """Approved workers with a fresh heartbeat."""
    try:
        from backend.racon.api.coordinator_state import get_coordinator_state
        from protocols import TrustState

        state = get_coordinator_state()
        now = time.time()
        return [
            node
            for node in state.registry.list_nodes()
            if node.trust_state == TrustState.APPROVED
            and now - float(node.last_heartbeat or 0.0) <= ONLINE_WINDOW_SECONDS
        ]
    except Exception as exc:
        logger.debug("Worker inventory unavailable: %s", exc)
        return []


def _virtual_entry(
    model_id: str,
    description: str,
    *,
    available: bool,
    context_length: Optional[int] = None,
) -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "racon",
        "racon": {
            "virtual": True,
            "available": available,
            "installed": available,
            "context_length": context_length,
            "description": description,
        },
    }


def _network_context_length() -> Optional[int]:
    """The context ceiling this gateway accepts, not a model's trained window."""
    try:
        from racon.settings import get_settings

        return int(get_settings().context_size or 0) or None
    except Exception:
        return None


def network_model_entries() -> list[dict[str, Any]]:
    workers = online_workers()
    worker_models: dict[str, list[str]] = {}
    for node in workers:
        if "streaming_chat" not in (getattr(node, "features", None) or []):
            continue
        for name in getattr(node, "models", None) or []:
            text = str(name).strip()
            if text and is_advertisable_model(text):
                worker_models.setdefault(text, []).append(str(getattr(node, "node_id", "")))

    local_models: dict[str, Any] = {}
    try:
        from racon.services.engine_service import get_engine

        for model in get_engine().models.installed():
            if is_advertisable_model(str(model.id)):
                local_models[str(model.id)] = model
    except Exception as exc:
        logger.debug("Local model inventory unavailable: %s", exc)

    available = bool(workers) or bool(local_models)
    context_length = _network_context_length()
    entries: list[dict[str, Any]] = [
        _virtual_entry(
            "racon",
            "RACoN network: auto-select one worker for a query, or decompose a complex "
            "task across workers and merge the streamed results.",
            available=available,
            context_length=context_length,
        ),
        _virtual_entry(
            "racon-single",
            "Force one worker/model and stream its answer.",
            available=available,
            context_length=context_length,
        ),
        _virtual_entry(
            "racon-task",
            "Force planner decomposition across workers, then merge the results.",
            available=available,
            context_length=context_length,
        ),
        _virtual_entry(
            "racon-agent",
            "Together AI agentic layer: a decision model classifies chat vs task, the agent "
            "plans with tool calls, worker outputs are merged and reviewed, and progress is "
            "streamed in the racon sidecar.",
            available=True,
            context_length=context_length,
        ),
    ]

    for model_id, node_ids in sorted(worker_models.items()):
        entries.append(
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "racon",
                "racon": {
                    "virtual": False,
                    "source": "worker",
                    "workers": sorted(set(node_ids)),
                    "available": True,
                    "installed": True,
                },
            }
        )

    for model_id, model in sorted(local_models.items()):
        if any(entry["id"] == model_id for entry in entries):
            continue
        entries.append(
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "racon",
                "racon": {
                    "virtual": False,
                    "source": "local",
                    "runtime": getattr(model, "runtime", None),
                    "context_length": getattr(model, "context_length", None),
                    "size_bytes": getattr(model, "size_bytes", None),
                    "available": True,
                    "installed": True,
                },
            }
        )
    return entries


def merge_model_entries(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for entry in group:
            model_id = str(entry.get("id") or "")
            if model_id:
                merged[model_id] = entry
    return list(merged.values())
