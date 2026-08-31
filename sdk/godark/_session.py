"""HPKE sealed session lifecycle for encrypted trading with gdx-edge sequencer."""

from __future__ import annotations

import uuid

from ._hpke import TAG_LEN, SealedSession, nonce_from_u64, setup_session


class CryptoSession:
    """Per-connection HPKE sealed session."""

    def __init__(self) -> None:
        self._sealed: SealedSession | None = None
        self._pending_sealed: SealedSession | None = None
        self._pending_conn_id = 0
        self._send_counter = 1
        self._conn_id = 0
        self._seen_recv_nonces: set[int] = set()

    @property
    def is_established(self) -> bool:
        return self._sealed is not None

    @property
    def conn_id(self) -> int | None:
        return self._conn_id or None

    @property
    def next_nonce(self) -> int:
        """Peek at the next send nonce counter without advancing it."""
        return self._send_counter

    def setup(self, recipient_public: bytes, user_uuid: uuid.UUID, conn_id: int) -> bytes:
        """HPKE Base setup against the pinned sequencer public key.

        Returns the encapped key for ``hpke_setup``; the session is not
        established until :meth:`establish` confirms the peer reply.
        """
        if conn_id == 0:
            raise ValueError("HPKE conn_id must be non-zero")
        from ._hpke import info_for_conn

        info = info_for_conn(user_uuid.bytes, conn_id)
        encapped, sealed = setup_session(recipient_public, info)
        self._pending_sealed = sealed
        self._pending_conn_id = conn_id
        return encapped

    def establish(self) -> None:
        """Commit a pending HPKE setup after the sequencer confirms."""
        if self._pending_sealed is None:
            raise RuntimeError("HPKE setup not pending peer confirmation")
        self._sealed = self._pending_sealed
        self._conn_id = self._pending_conn_id
        self._pending_sealed = None
        self._pending_conn_id = 0
        self._send_counter = 1
        self._seen_recv_nonces.clear()

    def abort_setup(self) -> None:
        """Discard a pending HPKE setup (timeout, rejection, or mismatch)."""
        self._pending_sealed = None
        self._pending_conn_id = 0

    @staticmethod
    def setup_rest(recipient_public: bytes, user_uuid: uuid.UUID, request_id: int):
        """One-shot REST HPKE (order header uses conn_id=0)."""
        from ._hpke import SealedSession, info_for_rest_request, setup_session

        encapped, sealed = setup_session(
            recipient_public, info_for_rest_request(user_uuid.bytes, request_id)
        )
        return encapped, sealed

    def encrypt_order(self, aad: bytes, plaintext: bytes) -> tuple[int, bytes]:
        """Encrypt an order payload. Returns (nonce_counter, ciphertext)."""
        sealed = self._require_sealed()
        nonce = self._send_counter
        if nonce == 0xFFFFFFFFFFFFFFFF:
            raise OverflowError("send nonce overflow")
        ct = sealed.seal_c2s(nonce_from_u64(nonce), aad, plaintext)
        self._send_counter = nonce + 1
        return nonce, ct

    def decrypt_push(self, nonce: int, aad: bytes, ciphertext: bytes) -> bytes:
        """Decrypt an encrypted_push from the sequencer."""
        sealed = self._require_sealed()
        if nonce in self._seen_recv_nonces:
            raise ValueError(f"replay detected: push nonce {nonce} already seen")
        pt = sealed.open_s2c(nonce_from_u64(nonce), aad, ciphertext)
        self._seen_recv_nonces.add(nonce)
        return pt

    @staticmethod
    def body_length_for_plaintext(plaintext_length: int) -> int:
        """Ciphertext size of an HPKE-sealed frame."""
        return plaintext_length + TAG_LEN

    def reset(self) -> None:
        """Reset session state (for reconnect or rekey)."""
        self._sealed = None
        self._pending_sealed = None
        self._pending_conn_id = 0
        self._send_counter = 1
        self._conn_id = 0
        self._seen_recv_nonces.clear()

    def _require_sealed(self) -> SealedSession:
        if self._sealed is None:
            raise RuntimeError("HPKE session not established")
        return self._sealed
