"""Read-only Supabase client for the RACoN control-plane catalog.

RACoN never issues DDL and never mutates the shared schema from the desktop app. The
client is used for health reporting and for reading the model registry. Credentials come
from settings/environment and are never returned to callers or written to logs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from racon.settings import get_settings

logger = logging.getLogger(__name__)

TRACKED_TABLES = ("models", "model_versions", "nodes", "scheduler_decisions")


class SupabaseError(RuntimeError):
    """A read-only Supabase request failed."""


@dataclass
class SupabaseConfig:
    url: str
    key: str
    key_kind: str

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)


def resolve_config() -> SupabaseConfig:
    settings = get_settings()
    url = (settings.supabase_url or "").strip().rstrip("/")
    secret = (settings.supabase_secret_key or "").strip()
    publishable = (settings.supabase_publishable_key or "").strip()
    if secret:
        return SupabaseConfig(url=url, key=secret, key_kind="secret")
    return SupabaseConfig(url=url, key=publishable, key_kind="publishable")


class SupabaseCatalogClient:
    """Minimal read-only client for the shared RACoN control-plane project."""

    def __init__(
        self,
        config: Optional[SupabaseConfig] = None,
        *,
        timeout: float = 15.0,
        client_factory=None,
    ) -> None:
        self.config = config or resolve_config()
        self.timeout = timeout
        self._client_factory = client_factory or self._default_client

    @property
    def configured(self) -> bool:
        return self.config.configured

    def _default_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "apikey": self.config.key,
                "Authorization": f"Bearer {self.config.key}",
                "Accept": "application/json",
                "User-Agent": "RACoN/1.0",
            },
        )

    @staticmethod
    def host_of(url: str) -> str:
        return url.split("//")[-1].split("/")[0] if url else ""

    async def status(self) -> dict[str, Any]:
        """Report connectivity and row counts without exposing credentials."""
        if not self.configured:
            return {
                "configured": False,
                "reachable": False,
                "host": "",
                "key_kind": "",
                "tables": {},
                "error": "Supabase is not configured.",
            }

        tables: dict[str, Any] = {}
        reachable = True
        error: Optional[str] = None
        try:
            async with self._client_factory() as client:
                for table in TRACKED_TABLES:
                    response = await client.get(
                        f"{self.config.url}/rest/v1/{table}",
                        params={"select": "id", "limit": "1"},
                        headers={"Prefer": "count=exact"},
                    )
                    if response.status_code >= 400:
                        reachable = False
                        tables[table] = {"status": response.status_code}
                        continue
                    content_range = response.headers.get("content-range", "")
                    count = content_range.split("/")[-1] if "/" in content_range else "?"
                    tables[table] = {"status": response.status_code, "count": count}
        except Exception as exc:
            reachable = False
            error = f"{type(exc).__name__}: {exc}"

        if not error and not reachable:
            error = "One or more registry tables rejected the request."

        return {
            "configured": True,
            "reachable": reachable,
            "host": self.host_of(self.config.url),
            "key_kind": self.config.key_kind,
            "tables": tables,
            "error": error,
        }

    async def list_models(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read the listed, active model registry entries.

        Only metadata is returned. Download URLs are not invented when the registry has no
        model versions or endpoints for a model.
        """
        if not self.configured:
            return []
        try:
            async with self._client_factory() as client:
                response = await client.get(
                    f"{self.config.url}/rest/v1/models",
                    params={
                        "select": "id,name,family,description,context_length,parameter_count,"
                        "quantization,status,visibility,is_listed,provider_label",
                        "is_listed": "eq.true",
                        "status": "eq.active",
                        "limit": str(max(1, min(limit, 200))),
                    },
                )
        except httpx.HTTPError as exc:
            raise SupabaseError(f"Could not reach Supabase: {exc}") from exc
        if response.status_code >= 400:
            raise SupabaseError(f"Supabase returned HTTP {response.status_code}.")
        try:
            rows = response.json()
        except ValueError as exc:
            raise SupabaseError("Supabase returned a non-JSON response.") from exc

        models: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            models.append(
                {
                    "id": str(row["id"]),
                    "name": str(row.get("name") or row["id"]),
                    "family": row.get("family"),
                    "description": row.get("description"),
                    "context_length": row.get("context_length"),
                    "parameter_count": row.get("parameter_count"),
                    "quantization": row.get("quantization"),
                    "provider_label": row.get("provider_label"),
                    "installable": False,
                }
            )
        return models

    async def list_endpoints(self, model_ids: list[str], limit: int = 200) -> dict[str, list[dict]]:
        """Read endpoint metadata for the given model ids."""
        if not self.configured or not model_ids:
            return {}
        ids = ",".join(model_ids[:50])
        try:
            async with self._client_factory() as client:
                response = await client.get(
                    f"{self.config.url}/rest/v1/model_endpoints",
                    params={
                        "select": "id,model_id,kind,method,path,status,supported_parameters",
                        "model_id": f"in.({ids})",
                        "limit": str(max(1, min(limit, 500))),
                    },
                )
        except httpx.HTTPError as exc:
            raise SupabaseError(f"Could not reach Supabase: {exc}") from exc
        if response.status_code >= 400:
            raise SupabaseError(f"Supabase returned HTTP {response.status_code}.")
        rows = response.json() if response.content else []
        grouped: dict[str, list[dict]] = {}
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("model_id"):
                grouped.setdefault(str(row["model_id"]), []).append(row)
        return grouped


_GLOBAL_CLIENT: Optional[SupabaseCatalogClient] = None


def get_supabase_client() -> SupabaseCatalogClient:
    global _GLOBAL_CLIENT
    if _GLOBAL_CLIENT is None:
        _GLOBAL_CLIENT = SupabaseCatalogClient()
    return _GLOBAL_CLIENT


def reset_supabase_client() -> None:
    global _GLOBAL_CLIENT
    _GLOBAL_CLIENT = None
