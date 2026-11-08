"""Operator API for RACoN inference keys."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.racon.network.api_keys import get_api_key_service
from ..storage.db import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/keys", tags=["API Keys"])


class CreateApiKeyRequest(BaseModel):
    name: str = Field(default="client", min_length=1, max_length=128)
    rate_limit_per_min: int = Field(default=60, ge=1, le=10000)
    scopes: list[str] = Field(default_factory=lambda: ["inference"], max_length=8)


@router.post("")
def create_api_key(request: CreateApiKeyRequest) -> dict:
    scopes = [scope.strip() for scope in request.scopes if scope.strip()] or ["inference"]
    if "inference" not in scopes:
        scopes.append("inference")
    record, token = get_api_key_service().create(
        request.name,
        scopes=scopes,
        rate_limit_per_min=request.rate_limit_per_min,
    )
    try:
        get_database().log_audit(
            "API_KEY_CREATED",
            f"Created inference key {record.id} ({record.name})",
        )
    except Exception:
        pass
    return record.to_dict(include_key=token)


@router.get("")
def list_api_keys() -> dict:
    keys = get_api_key_service().list()
    return {"object": "list", "data": [record.to_dict() for record in keys]}


@router.delete("/{key_id}")
def revoke_api_key(key_id: str) -> dict:
    revoked = get_api_key_service().revoke(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail=f"Key '{key_id}' not found or already revoked")
    try:
        get_database().log_audit("API_KEY_REVOKED", f"Revoked inference key {key_id}")
    except Exception:
        pass
    return {"id": key_id, "revoked": True}
