"""Optional cloud control-plane endpoints (read-only)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from racon.cloud.supabase import SupabaseError, get_supabase_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/cloud", tags=["Cloud"])


@router.get("/status")
async def cloud_status():
    """Report registry connectivity and row counts. Never returns credentials."""
    return await get_supabase_client().status()


@router.get("/models")
async def cloud_models(limit: int = Query(50, ge=1, le=200)):
    """Read the shared model registry.

    Entries are metadata only. ``installable`` stays false until the registry has a model
    version with a verified artifact; RACoN does not invent download URLs.
    """
    client = get_supabase_client()
    if not client.configured:
        return {"configured": False, "models": [], "endpoints": {}, "installable_count": 0}

    try:
        models = await client.list_models(limit=limit)
        endpoints = await client.list_endpoints([model["id"] for model in models])
    except SupabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "configured": True,
        "models": models,
        "endpoints": endpoints,
        "installable_count": sum(1 for model in models if model.get("installable")),
    }
