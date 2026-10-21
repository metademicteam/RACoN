"""Supabase cloud storage and synchronization adapter.

Provides safe REST client operations over HTTPX with graceful offline fallbacks
ensuring local cluster operations are never blocked when the network is unreachable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

import httpx

from backend.racon.core.config import get_settings

logger = logging.getLogger(__name__)


class SupabaseAdapter:
    """HTTP client adapter for Supabase cloud synchronization."""

    def __init__(
        self,
        url: Optional[str] = None,
        key: Optional[str] = None,
        default_timeout: float = 5.0,
    ) -> None:
        settings = get_settings()
        self.url = (url or settings.SUPABASE_URL or "").strip().rstrip("/")
        self.key = (key or settings.SUPABASE_KEY or "").strip()
        self.default_timeout = default_timeout

    @property
    def is_configured(self) -> bool:
        """Check whether valid Supabase credentials and URL are present."""
        return bool(
            self.url
            and self.url.startswith(("http://", "https://"))
            and self.key
            and len(self.key) > 10
        )

    def _get_headers(self) -> Dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def healthcheck(self, timeout_seconds: Optional[float] = None) -> bool:
        """Perform a non-blocking connectivity check against Supabase.

        Returns True if reachable, False if offline, timed out, or unconfigured.
        Never raises exceptions to callers.
        """
        if not self.is_configured:
            logger.debug("Supabase adapter is not configured; healthcheck skipped.")
            return False

        timeout = timeout_seconds or self.default_timeout
        endpoint = f"{self.url}/rest/v1/"

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(endpoint, headers=self._get_headers())
                # Supabase REST root responds with OpenAPI JSON specification (status 200)
                if resp.status_code in (200, 204):
                    return True
                logger.warning(
                    "Supabase healthcheck responded with unexpected status code: %d",
                    resp.status_code,
                )
                return False
        except httpx.TimeoutException:
            logger.warning("Supabase healthcheck timed out after %.1fs (offline mode active)", timeout)
            return False
        except httpx.RequestError as exc:
            logger.warning("Supabase healthcheck failed (network offline): %s", exc)
            return False
        except Exception as exc:
            logger.warning("Supabase healthcheck unexpected error: %s", exc)
            return False

    async def async_healthcheck(self, timeout_seconds: Optional[float] = None) -> bool:
        """Asynchronous non-blocking connectivity check against Supabase."""
        if not self.is_configured:
            return False

        timeout = timeout_seconds or self.default_timeout
        endpoint = f"{self.url}/rest/v1/"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(endpoint, headers=self._get_headers())
                return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Supabase async healthcheck failed: %s", exc)
            return False

    def query(
        self,
        table_name: str,
        select: str = "*",
        filters: Optional[Dict[str, str]] = None,
        limit: int = 100,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Query rows from a Supabase table.

        Returns None if offline, unconfigured, or on query error without throwing.
        """
        if not self.is_configured:
            logger.debug("Supabase unconfigured; query on '%s' skipped.", table_name)
            return None

        timeout = timeout_seconds or self.default_timeout
        endpoint = f"{self.url}/rest/v1/{table_name}"
        params: Dict[str, Union[str, int]] = {"select": select, "limit": limit}
        if filters:
            params.update(filters)

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(endpoint, headers=self._get_headers(), params=params)
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else [data]
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Supabase query on '%s' failed safely: %s", table_name, exc)
            return None
        except Exception as exc:
            logger.warning("Supabase query unexpected error on '%s': %s", table_name, exc)
            return None

    def insert(
        self,
        table_name: str,
        payload: Union[Dict[str, Any], List[Dict[str, Any]]],
        upsert: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Insert or upsert records to a Supabase table.

        Returns inserted rows or None if offline or on error without throwing.
        """
        if not self.is_configured:
            logger.debug("Supabase unconfigured; insert on '%s' skipped.", table_name)
            return None

        timeout = timeout_seconds or self.default_timeout
        endpoint = f"{self.url}/rest/v1/{table_name}"
        headers = self._get_headers()
        headers["Prefer"] = "return=representation"
        if upsert:
            headers["Prefer"] += ",resolution=merge-duplicates"

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(endpoint, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else [data]
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Supabase insert on '%s' failed safely: %s", table_name, exc)
            return None
        except Exception as exc:
            logger.warning("Supabase insert unexpected error on '%s': %s", table_name, exc)
            return None


_supabase_adapter_instance: Optional[SupabaseAdapter] = None


def get_supabase_adapter() -> SupabaseAdapter:
    """Return the singleton instance of the Supabase adapter."""
    global _supabase_adapter_instance
    if _supabase_adapter_instance is None:
        _supabase_adapter_instance = SupabaseAdapter()
    return _supabase_adapter_instance
