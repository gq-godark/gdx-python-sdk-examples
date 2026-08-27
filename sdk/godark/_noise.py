"""Noise_XK_25519_AESGCM_SHA256 primitives for the gdx WebSocket protocol."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

NOISE_PATTERN = b"Noise_XK_25519_AESGCM_SHA256"
PROLOGUE_DOMAIN = b"gdx-noise-xk/v1\x00"
HASH_LEN = 32
KEY_LEN = 32
TAG_LEN = 16
MAX_NOISE_MESSAGE_LEN = 65_535


def prologue_for_user(user_uuid: bytes) -> bytes:
    """Return the user-bound Noise prologue."""
    if len(user_uuid) != 16:
        raise ValueError(f"user_uuid must be 16 bytes, got {len(user_uuid)}")
    return PROLOGUE_DOMAIN + user_uuid


def parse_pinned_static_pubkey_hex(value: str) -> bytes:
    """Parse a 32-byte X25519 public key represented as hexadecimal."""
    value = value.strip().removeprefix("0x").removeprefix("0X")
    if len(value) != 64:
        raise ValueError("Noise static public key must be 64 hex chars (32-byte X25519 pubkey)")
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("Noise static public key must be valid hexadecimal") from exc
    if len(key) != KEY_LEN:
        raise ValueError("Noise static public key must be 32 bytes")
    return key


def pinned_sequencer_static_pub(explicit_hex: str | None = None) -> bytes:
    """Resolve the pinned sequencer key from an argument or supported env vars."""
    value = explicit_hex or next(
        (
            os.environ[name].strip()
            for name in (
                "GODARK_NOISE_STATIC_PUBLIC_KEY",
                "GDX_NOISE_STATIC_PUBLIC_KEY",
                "GDX_NOISE_STATIC_PUBKEY",
            )
            if os.environ.get(name, "").strip()
        ),
        "",
    )
    if not value:
        raise ValueError(
            "Noise static public key unset — pass noise_static_public_key_hex "
            "or set GODARK_NOISE_STATIC_PUBLIC_KEY"
        )
    return parse_pinned_static_pubkey_hex(value)


def _hash(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _hkdf(chaining_key: bytes, input_key_material: bytes, outputs: int) -> tuple[bytes, ...]:
    """Noise HKDF: HMAC-SHA256 extract, followed by 2 or 3 expand blocks."""
    temp_key = hmac.new(chaining_key, input_key_material, hashlib.sha256).digest()
    result: list[bytes] = []
    previous = b""
    for counter in range(1, outputs + 1):
        previous = hmac.new(temp_key, previous + bytes([counter]), hashlib.sha256).digest()
        result.append(previous)
    return tuple(result)


def _noise_nonce(counter: int) -> bytes:
    if not 0 <= counter <= 0xFFFFFFFFFFFFFFFF:
        raise OverflowError("Noise nonce counter exceeded u64 max")
    return b"\x00" * 4 + counter.to_bytes(8, "big")


@dataclass(frozen=True)
class NoiseKeyPair:
    private: X25519PrivateKey
    public: bytes


def generate_keypair() -> NoiseKeyPair:
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return NoiseKeyPair(private, public)


def _dh(private: X25519PrivateKey, public: bytes) -> bytes:
    if len(public) != KEY_LEN:
        raise ValueError("X25519 public key must be 32 bytes")
    shared = private.exchange(X25519PublicKey.from_public_bytes(public))
    if shared == b"\x00" * KEY_LEN:
        raise ValueError("weak X25519 public key: all-zero shared secret")
    return shared


class CipherState:
    """Noise CipherState; decrypt advances only after authentication succeeds."""

    def __init__(self) -> None:
        self._key: bytes | None = None
        self.nonce = 0

    def initialize_key(self, key: bytes | None) -> None:
        if key is not None and len(key) != KEY_LEN:
            raise ValueError("Noise cipher key must be 32 bytes")
        self._key = key
        self.nonce = 0

    @property
    def has_key(self) -> bool:
        return self._key is not None

    def encrypt_with_ad(self, aad: bytes, plaintext: bytes) -> bytes:
        if self._key is None:
            return plaintext
        if len(plaintext) + TAG_LEN > MAX_NOISE_MESSAGE_LEN:
            raise ValueError("plaintext exceeds max Noise message size")
        ciphertext = AESGCM(self._key).encrypt(_noise_nonce(self.nonce), plaintext, aad)
        self.nonce += 1
        return ciphertext

    def decrypt_with_ad(self, aad: bytes, ciphertext: bytes) -> bytes:
        if self._key is None:
            return ciphertext
        if not TAG_LEN <= len(ciphertext) <= MAX_NOISE_MESSAGE_LEN:
            raise ValueError("invalid Noise ciphertext length")
        plaintext = AESGCM(self._key).decrypt(_noise_nonce(self.nonce), ciphertext, aad)
        self.nonce += 1
        return plaintext

    def decrypt_with_ad_at(self, explicit_nonce: int, aad: bytes, ciphertext: bytes) -> bytes:
        """Decrypt at an explicit AEAD nonce without relying on the internal counter."""
        if self._key is None:
            return ciphertext
        if not TAG_LEN <= len(ciphertext) <= MAX_NOISE_MESSAGE_LEN:
            raise ValueError("invalid Noise ciphertext length")
        return AESGCM(self._key).decrypt(_noise_nonce(explicit_nonce), ciphertext, aad)


class _SymmetricState:
    def __init__(self) -> None:
        self.h = NOISE_PATTERN.ljust(HASH_LEN, b"\x00")
        self.ck = self.h
        self.cs = CipherState()

    def mix_key(self, input_key_material: bytes) -> None:
        self.ck, key = _hkdf(self.ck, input_key_material, 2)
        self.cs.initialize_key(key)

    def mix_hash(self, data: bytes) -> None:
        self.h = _hash(self.h + data)

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        ciphertext = self.cs.encrypt_with_ad(self.h, plaintext)
        self.mix_hash(ciphertext)
        return ciphertext

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        plaintext = self.cs.decrypt_with_ad(self.h, ciphertext)
        self.mix_hash(ciphertext)
        return plaintext

    def split(self) -> tuple[CipherState, CipherState]:
        key1, key2 = _hkdf(self.ck, b"", 2)
        first, second = CipherState(), CipherState()
        first.initialize_key(key1)
        second.initialize_key(key2)
        return first, second


class NoiseTransport:
    """Post-handshake initiator transport: first cipher sends, second receives."""

    def __init__(self, send: CipherState, recv: CipherState) -> None:
        self._send = send
        self._recv = recv

    @property
    def recv_nonce(self) -> int:
        return self._recv.nonce

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._send.encrypt_with_ad(b"", plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        return self._recv.decrypt_with_ad(b"", ciphertext)

    def decrypt_at_nonce(self, stamped_nonce: int, ciphertext: bytes) -> bytes:
        """Decrypt a receive frame at the server-stamped AEAD nonce (tolerates skipped frames).

        The sender stamps the true nonce on every push and the relay may drop messages, so aligning
        to the stamped value avoids the permanent AEAD desync a strictly-sequential counter would
        suffer. The internal receive counter is advanced past the stamped nonce so any later
        fallback to sequential decrypt stays consistent.
        """
        plaintext = self._recv.decrypt_with_ad_at(stamped_nonce, b"", ciphertext)
        self._recv.nonce = stamped_nonce + 1
        return plaintext

    def reset(self) -> None:
        self._send.initialize_key(None)
        self._recv.initialize_key(None)


class HandshakeInitiator:
    """Noise XK initiator for the gdx sequencer's pinned static key."""

    def __init__(self, remote_static_public: bytes, prologue: bytes) -> None:
        if len(remote_static_public) != KEY_LEN:
            raise ValueError("remote static public key must be 32 bytes")
        self._s = generate_keypair()
        self._e: NoiseKeyPair | None = None
        self._remote_static = remote_static_public
        self._remote_ephemeral: bytes | None = None
        self._state = _SymmetricState()
        self._state.mix_hash(prologue)
        self._state.mix_hash(remote_static_public)  # XK pre-message: <- s
        self._turn = 0
        self._finished = False

    def write_message(self, payload: bytes = b"") -> bytes:
        if self._turn == 0:
            self._e = generate_keypair()
            self._state.mix_hash(self._e.public)
            self._state.mix_key(_dh(self._e.private, self._remote_static))  # es
            self._turn = 1
            return self._e.public + self._state.encrypt_and_hash(payload)
        if self._turn == 2:
            if self._e is None or self._remote_ephemeral is None:
                raise RuntimeError("handshake missing remote ephemeral")
            encrypted_static = self._state.encrypt_and_hash(self._s.public)
            self._state.mix_key(_dh(self._s.private, self._remote_ephemeral))  # se
            self._finished = True
            self._turn = 3
            return encrypted_static + self._state.encrypt_and_hash(payload)
        raise RuntimeError("handshake: not initiator write turn")

    def read_message(self, message: bytes) -> bytes:
        if self._turn != 1:
            raise RuntimeError("handshake: not initiator read turn")
        if self._e is None or len(message) < KEY_LEN:
            raise ValueError("handshake: invalid responder message")
        self._remote_ephemeral = message[:KEY_LEN]
        self._state.mix_hash(self._remote_ephemeral)
        self._state.mix_key(_dh(self._e.private, self._remote_ephemeral))  # ee
        self._turn = 2
        return self._state.decrypt_and_hash(message[KEY_LEN:])

    def into_transport(self) -> NoiseTransport:
        if not self._finished:
            raise RuntimeError("handshake not finished")
        return NoiseTransport(*self._state.split())


def encrypt_bound(transport: NoiseTransport, aad: bytes, plaintext: bytes) -> bytes:
    """Encrypt ``SHA256(aad) || plaintext`` with empty Noise transport AD."""
    return transport.encrypt(_hash(aad) + plaintext)


def decrypt_bound(
    transport: NoiseTransport,
    aad: bytes,
    ciphertext: bytes,
    stamped_nonce: int | None = None,
) -> bytes:
    """Decrypt and authenticate a gdx bound-AEAD frame.

    When ``stamped_nonce`` is provided, decrypt at that server-stamped AEAD nonce (tolerant of
    relay-dropped frames) instead of the strictly-sequential internal receive counter.
    """
    if stamped_nonce is not None:
        framed = transport.decrypt_at_nonce(stamped_nonce, ciphertext)
    else:
        framed = transport.decrypt(ciphertext)
    if len(framed) < HASH_LEN:
        raise ValueError("bound ciphertext too short")
    actual, plaintext = framed[:HASH_LEN], framed[HASH_LEN:]
    if not hmac.compare_digest(actual, _hash(aad)):
        raise ValueError("bound AAD mismatch")
    return plaintext
