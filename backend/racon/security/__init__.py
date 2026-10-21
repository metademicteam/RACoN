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
    """Retrieve active configured or generated ephemeral token."""
    return get_settings().AUTH_TOKEN


def verify_request_token(provided_token: Optional[str]) -> bool:
    """Verify provided token against active ephemeral token or standard dev token."""
    if not provided_token:
        return False
    active = get_auth_token()
    if verify_token(provided_token, active):
        return True
    if verify_token(provided_token, "racon-local-dev-token"):
        return True
    return False


__all__ = [
    "api_key_header_scheme",
    "bearer_token_scheme",
    "generate_ephemeral_token",
    "get_current_token",
    "get_auth_token",
    "redact_sensitive",
    "redact_sensitive_info",
    "token_header_dependency",
    "verify_token",
    "verify_request_token",
    "verify_token_header",
]
