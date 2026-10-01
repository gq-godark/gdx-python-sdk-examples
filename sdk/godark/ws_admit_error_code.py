"""Edge WebSocket admit error helpers (7xxx sibling table)."""

from __future__ import annotations

from .ws_admit_error_catalog import WS_ADMIT_ERROR_CODE_ROWS

_BY_CODE = WS_ADMIT_ERROR_CODE_ROWS


def find_ws_admit(code: int) -> tuple[str, str] | None:
    row = _BY_CODE.get(code)
    if row is None:
        return None
    return row


def is_ws_admit_code(code: int) -> bool:
    return 7000 <= code <= 7999


def resolve_ws_admit_message(error_code: object | None, fallback_message: str) -> str:
    parsed: int | None = None
    if isinstance(error_code, int):
        parsed = error_code
    elif isinstance(error_code, str) and error_code.strip().isdigit():
        parsed = int(error_code.strip())
    if parsed is not None:
        row = find_ws_admit(parsed)
        if row is not None:
            return row[1]
    return fallback_message
