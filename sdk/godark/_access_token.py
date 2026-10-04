"""Helpers for GoDark REST access JWTs from ``POST /api/v1/auth/token``."""

from __future__ import annotations

import base64
import json

from ._identity import account_to_bytes


def account_from_access_token_jwt(token: str) -> str | None:
    """Parse the canonical account from JWT ``sub`` (signature not verified)."""
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
        account_to_bytes(sub)
    except ValueError:
        return None
    return sub


def user_uuid_from_access_token_jwt(token: str) -> str | None:
    """Deprecated alias for :func:`account_from_access_token_jwt`."""
    return account_from_access_token_jwt(token)
