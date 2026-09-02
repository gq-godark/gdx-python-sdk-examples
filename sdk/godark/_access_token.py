"""Helpers for GoDark REST access JWTs from ``POST /api/v1/auth/token``."""

from __future__ import annotations

import base64
import json
import uuid


def user_uuid_from_access_token_jwt(token: str) -> uuid.UUID | None:
    """Parse internal user UUID from JWT ``sub`` (signature not verified)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "==")
        claims = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        return None
    sub = claims.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None
