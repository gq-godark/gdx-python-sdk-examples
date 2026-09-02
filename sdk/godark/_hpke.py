"""HPKE Base (RFC 9180) for trading E2E — matches gdx_crypto::hpke.

Suite: DHKEM(X25519, HKDF-SHA256) + HKDF-SHA256 + AES-256-GCM.
After setup, peers export directional keys and seal each message with an
explicit 96-bit nonce (``0u32_be ‖ counter_be``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pyhpke import AEADId, CipherSuite, KDFId, KEMId
from pyhpke.keys.x25519_key import X25519Key

KEY_LEN = 32
ENCAPPED_KEY_LEN = 32
TAG_LEN = 16
WIRE_VERSION = 2

INFO_DOMAIN = b"gdx-hpke/v1\0"
INFO_DOMAIN_REST = b"gdx-hpke/v1/rest\0"
EXPORT_C2S = b"gdx-hpke/v1 c2s"
EXPORT_S2C = b"gdx-hpke/v1 s2c"

_SUITE = CipherSuite.new(
    kem_id=KEMId.DHKEM_X25519_HKDF_SHA256,
    kdf_id=KDFId.HKDF_SHA256,
    aead_id=AEADId.AES256_GCM,
)


def info_for_conn(user_uuid: bytes, conn_id: int) -> bytes:
    """``gdx-hpke/v1\\0 ‖ user_uuid ‖ conn_id_be``."""
    if len(user_uuid) != 16:
        raise ValueError(f"user_uuid must be 16 bytes, got {len(user_uuid)}")
    return INFO_DOMAIN + user_uuid + conn_id.to_bytes(8, "big")


def info_for_rest_request(user_uuid: bytes, request_id: int) -> bytes:
    """``gdx-hpke/v1/rest\\0 ‖ user_uuid ‖ request_id_be``."""
    if len(user_uuid) != 16:
        raise ValueError(f"user_uuid must be 16 bytes, got {len(user_uuid)}")
    return INFO_DOMAIN_REST + user_uuid + request_id.to_bytes(8, "big")


def nonce_from_u64(counter: int) -> bytes:
    """Pack a monotonic u64 into a 96-bit GCM nonce: ``0u32_be ‖ counter_be``."""
    if not 0 <= counter <= 0xFFFFFFFFFFFFFFFF:
        raise OverflowError("HPKE nonce counter exceeded u64 max")
    return b"\x00" * 4 + counter.to_bytes(8, "big")


def _seal(key: bytes, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
    return AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, nonce: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


@dataclass
class SealedSession:
    """Application keys after HPKE export."""

    k_c2s: bytes
    k_s2c: bytes

    def seal_c2s(self, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
        return _seal(self.k_c2s, nonce, aad, plaintext)

    def open_c2s(self, nonce: bytes, aad: bytes, ciphertext: bytes) -> bytes:
        return _open(self.k_c2s, nonce, aad, ciphertext)

    def seal_s2c(self, nonce: bytes, aad: bytes, plaintext: bytes) -> bytes:
        return _seal(self.k_s2c, nonce, aad, plaintext)

    def open_s2c(self, nonce: bytes, aad: bytes, ciphertext: bytes) -> bytes:
        return _open(self.k_s2c, nonce, aad, ciphertext)


@dataclass
class StaticKeyPair:
    """Sequencer static recipient keypair (tests / mock edge)."""

    private: bytes
    public: bytes

    @classmethod
    def generate(cls) -> StaticKeyPair:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        sk = X25519PrivateKey.generate()
        return cls(
            private=sk.private_bytes_raw(),
            public=sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
        )


def setup_session(recipient_public: bytes, info: bytes) -> tuple[bytes, SealedSession]:
    """Client (initiator): encapsulate to sequencer pubkey."""
    if len(recipient_public) != KEY_LEN:
        raise ValueError(f"HPKE public key must be {KEY_LEN} bytes")
    pk = X25519Key.from_public_bytes(recipient_public)
    enc, ctx = _SUITE.create_sender_context(pk, info=info)
    return enc, SealedSession(
        k_c2s=ctx.export(EXPORT_C2S, KEY_LEN),
        k_s2c=ctx.export(EXPORT_S2C, KEY_LEN),
    )


def open_session(recipient: StaticKeyPair, encapped_key: bytes, info: bytes) -> SealedSession:
    """Sequencer (recipient): open encapped key with static private key."""
    if len(encapped_key) != ENCAPPED_KEY_LEN:
        raise ValueError(f"encapped key must be {ENCAPPED_KEY_LEN} bytes, got {len(encapped_key)}")
    sk = X25519Key.from_private_bytes(recipient.private)
    ctx = _SUITE.create_recipient_context(encapped_key, sk, info=info)
    return SealedSession(
        k_c2s=ctx.export(EXPORT_C2S, KEY_LEN),
        k_s2c=ctx.export(EXPORT_S2C, KEY_LEN),
    )


def parse_pinned_static_public_key(hex_str: str) -> bytes:
    """Parse a 32-byte X25519 public key represented as hexadecimal."""
    value = hex_str.strip().removeprefix("0x").removeprefix("0X")
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"HPKE static public key must be hex: {exc}") from exc
    if len(key) != KEY_LEN:
        raise ValueError(f"HPKE static public key must be {KEY_LEN} bytes, got {len(key)}")
    return key


def pinned_sequencer_static_pub(explicit_hex: str | None = None) -> bytes:
    """Resolve the pinned sequencer key from an argument or supported env vars."""
    value = explicit_hex or next(
        (
            os.environ[name].strip()
            for name in (
                "GDX_HPKE_STATIC_PUBLIC_KEY",
                "GDX_HPKE_STATIC_PUBKEY",
                "GODARK_HPKE_STATIC_PUBLIC_KEY",
                "VITE_GDX_HPKE_STATIC_PUBKEY",
                "GDX_HPKE_STATIC_PUBLIC_KEY",
                "GDX_HPKE_STATIC_PUBKEY",
                "GODARK_HPKE_STATIC_PUBLIC_KEY",
            )
            if os.environ.get(name, "").strip()
        ),
        "",
    )
    if not value:
        raise ValueError(
            "HPKE static public key unset — pass hpke_static_public_key_hex "
            "or set GDX_HPKE_STATIC_PUBLIC_KEY"
        )
    return parse_pinned_static_public_key(value)
