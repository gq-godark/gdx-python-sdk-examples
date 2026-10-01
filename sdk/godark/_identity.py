"""Account conversion helpers for the 32-byte GoDark L2 account wire key."""

from __future__ import annotations

import uuid

USER_UUID_LEN = 16
ACCOUNT_LEN = 32
USER_COMMITMENT_LEN = 32
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {char: index for index, char in enumerate(_BASE58_ALPHABET)}

PLACEHOLDER_USER_COMMITMENT: bytes = bytes(USER_COMMITMENT_LEN)
"""32 zero bytes. The SDK never computes the real commitment; the edge fills
it on the way to the sequencer. This matches gdx-web's PLACEHOLDER_USER_COMMITMENT."""


def uuid_to_bytes(s: str) -> bytes:
    """Canonical UUID string -> 16-byte big-endian wire encoding."""
    return uuid.UUID(s).bytes


def bytes_to_uuid(b: bytes) -> str:
    """16-byte big-endian -> canonical 8-4-4-4-12 hex string."""
    if len(b) != USER_UUID_LEN:
        raise ValueError(f"user_uuid must be {USER_UUID_LEN} bytes, got {len(b)}")
    return str(uuid.UUID(bytes=b))


def account_to_bytes(value: str | bytes) -> bytes:
    """Decode a base58 (or 64-char hexadecimal) account into 32 wire bytes."""
    if isinstance(value, bytes):
        if len(value) != ACCOUNT_LEN:
            raise ValueError(f"account must be {ACCOUNT_LEN} bytes, got {len(value)}")
        return value
    text = value.strip()
    hex_text = text.removeprefix("0x").removeprefix("0X")
    if len(hex_text) == ACCOUNT_LEN * 2:
        try:
            decoded = bytes.fromhex(hex_text)
        except ValueError:
            pass
        else:
            return decoded
    number = 0
    try:
        for char in text:
            number = number * 58 + _BASE58_INDEX[char]
    except KeyError as exc:
        raise ValueError(f"account is not valid base58: invalid character {exc.args[0]!r}") from exc
    leading_zeroes = len(text) - len(text.lstrip("1"))
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    decoded = b"\x00" * leading_zeroes + body
    if len(decoded) != ACCOUNT_LEN:
        raise ValueError(f"account must decode to {ACCOUNT_LEN} bytes, got {len(decoded)}")
    return decoded


def bytes_to_account(value: bytes) -> str:
    """Encode 32 account bytes using the canonical base58 representation."""
    if len(value) != ACCOUNT_LEN:
        raise ValueError(f"account must be {ACCOUNT_LEN} bytes, got {len(value)}")
    leading_zeroes = len(value) - len(value.lstrip(b"\x00"))
    number = int.from_bytes(value, "big")
    chars: list[str] = []
    while number:
        number, remainder = divmod(number, 58)
        chars.append(_BASE58_ALPHABET[remainder])
    return "1" * leading_zeroes + "".join(reversed(chars))
