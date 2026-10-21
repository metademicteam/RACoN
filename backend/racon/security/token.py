"""Security token generation, safe constant-time verification, and redaction."""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from typing import Any, Optional

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from backend.racon.core.config import get_settings

logger = logging.getLogger(__name__)

# FastAPI security schemes
api_key_header_scheme = APIKeyHeader(name="X-API-Token", auto_error=False)
bearer_token_scheme = HTTPBearer(auto_error=False)

# Common regex patterns for secret masking
_SENSITIVE_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"sb_publishable_[A-Za-z0-9_\-]+", re.IGNORECASE), "sb_publishable_[REDACTED]"),
    (re.compile(r"sb_secret_[A-Za-z0-9_\-]+", re.IGNORECASE), "sb_secret_[REDACTED]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|api[_-]?key|token|auth(?:orization)?)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.+=/]{6,})['\"]?"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),
        "[REDACTED_JWT]",
    ),
]


def generate_ephemeral_token(length: int = 32) -> str:
    """Generate a cryptographically secure URL-safe ephemeral token."""
    return secrets.token_urlsafe(length)


def verify_token(provided_token: Optional[str], expected_token: Optional[str]) -> bool:
    """Safely verify a token in constant time to prevent timing attacks.

    Returns True only if both tokens are non-empty and match exactly.
    """
    if not provided_token or not expected_token:
        return False
    # hmac.compare_digest operates in constant time
    return hmac.compare_digest(str(provided_token).strip(), str(expected_token).strip())


def redact_sensitive(text: Any) -> str:
    """Redact sensitive credentials, auth tokens, and secrets from string or log output."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # Apply static regex redaction patterns
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)

    # Redact configured dynamic tokens from settings if present
    try:
        settings = get_settings()
        if settings.AUTH_TOKEN and len(settings.AUTH_TOKEN) > 6:
            text = text.replace(settings.AUTH_TOKEN, "[REDACTED_AUTH_TOKEN]")
        if settings.SUPABASE_KEY and len(settings.SUPABASE_KEY) > 6:
            text = text.replace(settings.SUPABASE_KEY, "[REDACTED_SUPABASE_KEY]")
    except Exception:
        # Settings may not yet be initialized in some early logging phases
        pass

    return text


async def verify_token_header(
    header_token: Optional[str] = Security(api_key_header_scheme),
    bearer_creds: Optional[HTTPAuthorizationCredentials] = Security(bearer_token_scheme),
) -> str:
    """FastAPI dependency for verifying incoming request authorization.

    Accepts the token either from the `X-API-Token` header or from
    the `Authorization: Bearer <token>` header.
    Raises HTTPException(401) if missing or invalid.
    """
    token: Optional[str] = None
    if header_token:
        token = header_token.strip()
    elif bearer_creds and bearer_creds.credentials:
        token = bearer_creds.credentials.strip()

    expected_token = get_settings().AUTH_TOKEN

    if not token or not verify_token(token, expected_token):
        logger.warning("Unauthorized access attempt: invalid or missing token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


# Alias for dependency injection
get_current_token = verify_token_header
token_header_dependency = verify_token_header
