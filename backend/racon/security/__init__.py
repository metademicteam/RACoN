"""Security package for authentication, token validation, and secret redaction."""

from __future__ import annotations

from typing import Optional

from backend.racon.core.config import get_settings
from backend.racon.security.token import (
    api_key_header_scheme,
    bearer_token_scheme,
    generate_ephemeral_token,
    get_current_token,
    redact_sensitive,
    token_header_dependency,
    verify_token,
    verify_token_header,
)

# Friendly aliases and helper functions
redact_sensitive_info = redact_sensitive


def get_auth_token() -> str:
    """Retrieve active configured or generated ephemeral admin token."""
    return get_settings().AUTH_TOKEN


def get_client_token() -> str:
    """Retrieve the limited-scope client token, or an empty string when disabled."""
    return get_settings().CLIENT_TOKEN


def verify_request_token(provided_token: Optional[str]) -> bool:
    """Verify a caller token against the active admin token in constant time.

    The former unconditional acceptance of a hard-coded development token has been removed:
    a development bypass now exists only when ``RACON_DEV_MODE`` is enabled, which also makes
    the configured token that literal value.
    """
    if not provided_token:
        return False
    return verify_token(provided_token, get_auth_token())


def verify_client_token(provided_token: Optional[str]) -> bool:
    """Verify a caller token against the limited-scope client token."""
    client_token = get_client_token()
    if not provided_token or not client_token:
        return False
    return verify_token(provided_token, client_token)


def get_token_scope(provided_token: Optional[str]) -> Optional[str]:
    """Return ``"admin"``, ``"client"``, or ``None`` for a presented token."""
    if not provided_token:
        return None
    if verify_request_token(provided_token):
        return "admin"
    if verify_client_token(provided_token):
        return "client"
    return None


__all__ = [
    "api_key_header_scheme",
    "bearer_token_scheme",
    "generate_ephemeral_token",
    "get_current_token",
    "get_auth_token",
    "get_client_token",
    "get_token_scope",
    "redact_sensitive",
    "redact_sensitive_info",
    "token_header_dependency",
    "verify_client_token",
    "verify_token",
    "verify_request_token",
    "verify_token_header",
]
