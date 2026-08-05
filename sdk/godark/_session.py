"""Noise XK session lifecycle for encrypted trading with gdx-edge sequencer."""

import os

from ._noise import HASH_LEN, TAG_LEN, NoiseTransport, decrypt_bound, encrypt_bound

# When enabled (the default), encrypted pushes are decrypted at the server-stamped wire nonce
# rather than the strictly-sequential Noise receive counter, tolerating relay-dropped frames.
# The edge stamps the true nonce on every push, so the sequential assumption caused later pushes to
# stall forever after any skipped frame (command timeout while the order actually succeeded).
# Disable with GDX_STAMPED_NONCE_PUSH=false to restore the legacy strictly-sequential path.
STAMPED_NONCE_PUSH = os.environ.get("GDX_STAMPED_NONCE_PUSH", "true").lower() != "false"


class CryptoSession:
    """Manages a single completed Noise XK transport with the sequencer."""

    def __init__(self):
        self._transport: NoiseTransport | None = None
        self._conn_id: int | None = None
        self._send_nonce = 0
        self._established = False

    @property
    def is_established(self) -> bool:
        return self._established

    @property
    def conn_id(self) -> int | None:
        return self._conn_id

    @property
    def next_nonce(self) -> int:
        """Peek at the next send nonce counter without advancing it."""
        return self._send_nonce

    @property
    def recv_nonce(self) -> int:
        """Next Noise receive counter; used for ordered encrypted pushes."""
        return self._transport.recv_nonce if self._transport is not None else 0

    def establish(self, transport: NoiseTransport, conn_id: int) -> None:
        """Attach a completed Noise transport for the authenticated connection."""
        if conn_id == 0:
            raise ValueError("conn_id must be non-zero")
        self.reset()
        self._transport = transport
        self._conn_id = conn_id
        self._send_nonce = 0
        self._established = True

    def encrypt_order(self, aad: bytes, plaintext: bytes) -> tuple[int, bytes]:
        """
        Encrypt an order payload. Returns (nonce_counter, ciphertext).
        aad = protobuf-encoded OrderHeader bytes.
        """
        if not self._established:
            raise RuntimeError("Session not established")
        nonce_counter = self._send_nonce
        if nonce_counter > 0xFFFFFFFF:
            raise OverflowError("Send nonce counter exceeded u32 max")
        self._send_nonce += 1
        ct = encrypt_bound(self._require_transport(), aad, plaintext)
        expected_length = HASH_LEN + len(plaintext) + TAG_LEN
        if len(ct) != expected_length:
            raise RuntimeError(
                f"encrypt_order produced {len(ct)} bytes; expected {expected_length}"
            )
        return nonce_counter, ct

    def decrypt_push(self, nonce_counter: int, aad: bytes, ciphertext: bytes) -> bytes:
        """
        Decrypt an encrypted_push from the sequencer.
        aad = protobuf-encoded ResponseHeader bytes.
        """
        if not self._established:
            raise RuntimeError("Session not established")
        stamped = nonce_counter if STAMPED_NONCE_PUSH else None
        return decrypt_bound(self._require_transport(), aad, ciphertext, stamped)

    @staticmethod
    def body_length_for_plaintext(plaintext_length: int) -> int:
        """Ciphertext size of a bound-AEAD frame."""
        return HASH_LEN + plaintext_length + TAG_LEN

    def reset(self) -> None:
        """Reset session state (for reconnect or rekey)."""
        if self._transport is not None:
            self._transport.reset()
        self._transport = None
        self._conn_id = None
        self._send_nonce = 0
        self._established = False

    def _require_transport(self) -> NoiseTransport:
        if not self._established or self._transport is None:
            raise RuntimeError("Noise XK session not established")
        return self._transport
