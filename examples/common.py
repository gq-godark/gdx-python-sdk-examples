from __future__ import annotations

import os


def env_first(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return default


def env_bool(*keys: str, default: bool = False) -> bool:
    for key in keys:
        raw = os.environ.get(key, "").strip().lower()
        if not raw:
            continue
        return raw in {"1", "true", "yes", "on"}
    return default


def require_credentials() -> tuple[str, str]:
    key_id = env_first("GODARK_API_KEY_ID", "GDX_API_KEY_ID")
    secret = env_first("GODARK_API_SECRET", "GDX_API_SECRET")
    if not key_id or not secret:
        raise SystemExit(
            "Missing credentials. Set GODARK_API_KEY_ID and GODARK_API_SECRET "
            "(or GDX_API_KEY_ID/GDX_API_SECRET)."
        )
    return key_id, secret


def ws_base() -> str:
    return env_first("GODARK_EDGE_URL", "GDX_EDGE_URL", default="wss://api.godark-dex.com")


def rest_base() -> str:
    return env_first(
        "GODARK_REST_BASE",
        "GDX_REST_URL",
        default="https://api.godark-dex.com/api/v1",
    )
