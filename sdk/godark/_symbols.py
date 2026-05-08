"""Default trading symbol id map (same data as ``shared/symbols.json`` in the repo root)."""

from __future__ import annotations

import json
import warnings
from importlib import resources
from pathlib import Path

_FALLBACK: dict[str, int] = {
    "BTC-USDC-PERP": 1,
    "ETH-USDC-PERP": 2,
    "SOL-USDC-PERP": 5,
}

# Repo-relative path: src/godark/_symbols.py -> ../../shared/symbols.json
_MONOREPO_PATH = Path(__file__).resolve().parent / "../../shared/symbols.json"


def load_default_symbol_map() -> dict[str, int]:
    """Load symbol map: try package resources first, then monorepo path, then fallback."""
    # 1. Try importlib.resources (works in wheel installs)
    try:
        txt = resources.files("godark").joinpath("symbols.json").read_text(encoding="utf-8")
        data = json.loads(txt)
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except Exception:
        pass

    # 2. Try monorepo-relative path (works in editable/dev installs from source tree)
    try:
        if _MONOREPO_PATH.is_file():
            txt = _MONOREPO_PATH.read_text(encoding="utf-8")
            data = json.loads(txt)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
    except Exception:
        pass

    # 3. Hardcoded fallback -- warn so developers notice
    warnings.warn(
        "Could not load symbols.json from package or monorepo; using hardcoded fallback. "
        "Run `pip install -e .` from python/ or build a wheel to fix.",
        stacklevel=2,
    )
    return dict(_FALLBACK)
