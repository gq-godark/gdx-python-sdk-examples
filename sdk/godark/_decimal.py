"""Decimal-string encode/decode for sealed order prices and sizes.

Public SDK methods take/return floats; the sealed protobuf uses human decimal
strings scaled to each instrument's ``price_decimals`` / ``quantity_decimals``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def format_decimal(value: float | int | str | Decimal, decimals: int) -> str:
    """Format a public numeric value as a venue decimal string.

    Rejects non-finite floats and values with more fractional digits than
    ``decimals`` (never rounds). Pads with trailing zeros to exactly
    ``decimals`` places when ``decimals > 0``.
    """
    if decimals < 0:
        raise ValueError(f"decimals must be >= 0, got {decimals}")
    if isinstance(value, float) and (
        value != value or value in (float("inf"), float("-inf"))  # noqa: PLR0124
    ):
        raise ValueError(f"not a finite number: {value}")
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a decimal number: {value!r}") from exc

    # Reject over-precise fractions without rounding.
    _, _, exp = d.as_tuple()
    frac_digits = -exp if isinstance(exp, int) and exp < 0 else 0
    if frac_digits > decimals:
        raise ValueError(f"value {value!r} has more than {decimals} decimal places")

    if decimals == 0:
        return format(d.to_integral_value(), "f")

    # Pad to exactly ``decimals`` fractional digits.
    quant = Decimal(1).scaleb(-decimals)
    q = d.quantize(quant)
    return format(q, "f")


def parse_decimal(value: str | float | int | Decimal | None) -> float | None:
    """Decode a wire decimal string (or numeric) back to a public float."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"not a decimal number: {value!r}")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a decimal number: {value!r}") from exc


def parse_decimal_required(value: str | float | int | Decimal) -> float:
    """Like :func:`parse_decimal` but empty/None raises."""
    out = parse_decimal(value)
    if out is None:
        raise ValueError(f"not a decimal number: {value!r}")
    return out
