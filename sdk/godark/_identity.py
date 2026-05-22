"""UUID conversion helpers between canonical string form and 16-byte big-endian.

The wire format carries `bytes user_uuid` (RFC 4122, big-endian, 16 B) so it
matches the wire encoding (big-endian 16-byte UUID). The public Python API exposes the canonical
8-4-4-4-12 hex string because that's what users see in dashboards. Conversion
happens at the wire boundary.
"""

from __future__ import annotations

import uuid

USER_UUID_LEN = 16
USER_COMMITMENT_LEN = 32

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
