"""Minimal `.env` loader shared by MM example scripts (stdlib-only)."""

from __future__ import annotations

import os
from pathlib import Path

from godark.errors import OrderError


def load_dotenv() -> None:
    """Load ``.env`` from the repo root if present (OS env wins)."""
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
        if key and key not in os.environ:
            os.environ[key] = val


def print_order_error(operation: str, err: Exception) -> None:
    """Pretty-print order rejections (symbolic ``error_code`` when available)."""
    if isinstance(err, OrderError):
        code = err.error_code if err.error_code else "<none>"
        print(f"{operation}: OrderError code={code} reason={err.args[0]}", flush=True)
    else:
        print(f"{operation}: {err!r}", flush=True)
