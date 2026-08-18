"""Minimal `.env` loader shared by MM example scripts (stdlib-only)."""

from __future__ import annotations

import os
from pathlib import Path

from godark.errors import OrderError

# Keys present (non-blank) in the real process environment before `.env` merge.
_os_present: set[str] | None = None
_file_vals: dict[str, str] = {}


def get_first(*keys: str, default: str = "") -> str:
    """OS ``GODARK_*`` then OS ``GDX_*``, then the same order from ``.env``."""
    if _os_present is None:
        for key in keys:
            v = os.environ.get(key, "").strip()
            if v:
                return v
        return default
    for key in keys:
        if key in _os_present:
            v = os.environ.get(key, "").strip()
            if v:
                return v
    for key in keys:
        v = (_file_vals.get(key) or "").strip()
        if v:
            return v
    return default


def load_dotenv() -> None:
    """Load ``.env`` from the repo root if present (OS env wins per key)."""
    global _os_present, _file_vals
    if _os_present is not None:
        return
    _os_present = {k for k, v in os.environ.items() if str(v).strip()}
    _file_vals = {}
    root = Path(__file__).resolve().parent.parent
    path = root / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        _file_vals[key] = val
        if key not in os.environ:
            os.environ[key] = val


def print_order_error(operation: str, err: Exception) -> None:
    """Pretty-print order rejections (symbolic ``error_code`` when available)."""
    if isinstance(err, OrderError):
        code = err.error_code if err.error_code else "<none>"
        print(f"{operation}: OrderError code={code} reason={err.args[0]}", flush=True)
    else:
        print(f"{operation}: {err!r}", flush=True)
