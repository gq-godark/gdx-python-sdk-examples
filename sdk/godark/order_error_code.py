"""Canonical numeric order error codes (shared registry across GoDark SDKs)."""

from __future__ import annotations

from .errors import OrderError
from .order_error_catalog import ORDER_ERROR_CODES, OrderErrorEntry

_ORDER_BY_CODE = {e.code: e for e in ORDER_ERROR_CODES}


def find(code: int) -> OrderErrorEntry | None:
    """Look up an entry by its numeric wire code."""
    return _ORDER_BY_CODE.get(code)


def find_symbolic(symbolic: str) -> OrderErrorEntry | None:
    """Look up by SCREAMING_SNAKE_CASE symbolic name."""
    for entry in ORDER_ERROR_CODES:
        if entry.symbolic == symbolic:
            return entry
    return None


def make_order_error_from_code(numeric: int | None) -> OrderError:
    """Map protobuf `AckMessage.error_code` → rich :class:`~godark.errors.OrderError`."""
    if numeric is None:
        return OrderError("order rejected", error_code=None)
    raw = numeric
    if 0 <= raw <= 65535:
        entry = find(raw)
        if entry:
            msg = f"{entry.reason} ({entry.symbolic}, code={entry.code})"
            return OrderError(msg, error_code=entry.symbolic, user_message=entry.reason)
    return OrderError("order rejected", error_code=str(raw))


def make_order_error_from_json(
    reason: str | None,
    code: str | None,
) -> OrderError:
    """JSON ack path — wire may carry numeric or symbolic `error_code` strings."""
    final_reason = reason if reason else "order rejected"
    final_code = code

    if code and code.strip():
        stripped = code.strip()
        if stripped.lstrip("-").isdigit():
            parsed = int(stripped)
            if 0 <= parsed <= 65535:
                entry = find(parsed)
                if entry:
                    final_code = entry.symbolic
                    if not reason or reason == "order rejected":
                        final_reason = f"{entry.reason} ({entry.symbolic}, code={entry.code})"
            else:
                final_code = str(parsed)
        else:
            entry = find_symbolic(stripped)
            if entry and (not reason or reason == "order rejected"):
                final_reason = f"{entry.reason} ({entry.symbolic}, code={entry.code})"

    return OrderError(final_reason, error_code=final_code)
