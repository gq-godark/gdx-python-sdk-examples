"""Decimal-string encode/decode for sealed order prices and sizes.

Public SDK trading methods take human decimal *strings* only (e.g. ``"67500.0"``,
``"0.001"``). ``int``, ``float``, and ``bool`` are rejected with ``TypeError``;
there is no numeric coercion before seal. The sealed protobuf uses the same
decimal text, normalized to each instrument's ``price_decimals`` /
``quantity_decimals``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

__all__ = ["format_decimal", "parse_decimal", "parse_decimal_required"]


def _require_decimal_str(value: object) -> str:
    """Public trading amounts must be ``str``; reject int/float/bool and other types."""
    if not isinstance(value, str):
        raise TypeError(
            "decimal value must be str "
            f"(got {type(value).__name__}); int/float/bool are not accepted"
        )
    return value


def format_decimal(value: str, decimals: int) -> str:
    """Normalize a public decimal string for the wire.

    Accepts only ``str`` decimal text. ``int``, ``float``, and ``bool`` raise
    ``TypeError`` (no coercion). Also rejects empty input, non-decimal text,
    scientific notation, non-finite values, negatives, and values with more
    fractional digits than ``decimals`` (never rounds). Pads with trailing
    zeros to exactly ``decimals`` places when ``decimals > 0``.
    """
    value = _require_decimal_str(value)
    if decimals < 0:
        raise ValueError(f"decimals must be >= 0, got {decimals}")

    s = value.strip()
    if not s:
        raise ValueError("empty decimal string")
    if any(ch in s for ch in "eE"):
        raise ValueError(f"scientific notation not allowed: {value!r}")
    if s[0] in "+-":
        raise ValueError(f"signed decimal not allowed: {value!r}")

    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a decimal number: {value!r}") from exc

    if not d.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    if d < 0:
        raise ValueError(f"decimal value must be non-negative: {value!r}")

    # Reject over-precise fractions without rounding (use the input text so
    # trailing zeros in the caller's string count toward precision).
    frac_digits = len(s.split(".", 1)[1]) if "." in s else 0
    if frac_digits > decimals:
        raise ValueError(f"value {value!r} has more than {decimals} decimal places")

    if decimals == 0:
        return format(d.to_integral_value(), "f")

    # Pad to exactly ``decimals`` fractional digits.
    quant = Decimal(1).scaleb(-decimals)
    q = d.quantize(quant)
    return format(q, "f")


def parse_decimal(value: str | None) -> str | None:
    """Validate a wire decimal string and return the trimmed text (or None)."""
    if value is None:
        return None
    value = _require_decimal_str(value)
    s = value.strip()
    if not s:
        return None
    # Reuse format_decimal's validation with a permissive scale so callers get
    # a clear error for garbage; do not re-pad here.
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a decimal number: {value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    return s


def parse_decimal_required(value: str) -> str:
    """Like :func:`parse_decimal` but empty/None raises."""
    out = parse_decimal(value)
    if out is None:
        raise ValueError(f"not a decimal number: {value!r}")
    return out
