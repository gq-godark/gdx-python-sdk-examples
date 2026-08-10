"""Default trading symbol id map — offline fallback when edge fetch fails."""

from __future__ import annotations

import json
import logging
import warnings
from importlib import resources
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("godark.symbols")

_FALLBACK: dict[str, int] = {
    "BTC-USDC-PERP": 1,
    "ETH-USDC-PERP": 2,
    "SOL-USDC-PERP": 5,
}

# Repo-relative path: src/godark/_symbols.py -> ../../shared/symbols.json
_MONOREPO_PATH = Path(__file__).resolve().parent / "../../shared/symbols.json"


def parse_symbol_map_from_instruments(data: dict[str, Any]) -> dict[str, int]:
    """Build symbol → symbol_id from edge ``GET /api/v1/instruments`` data."""
    rows = data.get("instruments")
    if not isinstance(rows, list):
        raise ValueError("instruments response missing instruments array")
    out: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = row.get("symbol")
        sid = row.get("symbol_id")
        if isinstance(sym, str) and isinstance(sid, int):
            out[sym] = sid
    if not out:
        raise ValueError("instruments response contained no usable symbol rows")
    return out


def load_offline_symbol_map() -> dict[str, int]:
    """Load bundled/offline fallback map (tests or edge unreachable)."""
    try:
        txt = resources.files("godark").joinpath("symbols.json").read_text(encoding="utf-8")
        data = json.loads(txt)
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except Exception:
        pass

    try:
        if _MONOREPO_PATH.is_file():
            txt = _MONOREPO_PATH.read_text(encoding="utf-8")
            data = json.loads(txt)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
    except Exception:
        pass

    warnings.warn(
        "Could not load symbols.json from package or monorepo; using hardcoded fallback. "
        "Run `pip install -e .` from python/ or connect to edge for live instruments.",
        stacklevel=2,
    )
    return dict(_FALLBACK)


def load_default_symbol_map() -> dict[str, int]:
    """Backward-compatible alias for offline fallback map."""
    return load_offline_symbol_map()


async def load_symbol_map_from_edge(rest_base_url: str) -> dict[str, int]:
    """Fetch symbol map from edge; fall back to offline map on failure."""
    from ._rest_transport import RestTransport

    transport = RestTransport(rest_base_url)
    try:
        data = await transport.instruments_public()
        return parse_symbol_map_from_instruments(data)
    except Exception as exc:
        _LOG.warning("edge instruments fetch failed (%s); using offline fallback", exc)
        return load_offline_symbol_map()
    finally:
        await transport.aclose()
