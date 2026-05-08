"""ECDH session lifecycle for encrypted trading with gdx-edge sequencer."""

import base64

from . import _crypto


class CryptoSession:
    """Manages a single ECDH session with the sequencer."""

    def __init__(self):
        self._private_key: _crypto.X25519PrivateKey | None = None
        self._local_public: bytes | None = None
        self._session_key: bytes | None = None
        self._session_id: int | None = None
        self._nonce: _crypto.NonceTracker = _crypto.NonceTracker()
        self._established = False

    @property
    def is_established(self) -> bool:
        return self._established

    @property
    def session_id(self) -> int | None:
        return self._session_id

    @property
    def next_nonce(self) -> int:
        """Peek at the next send nonce counter without advancing it."""
        return self._nonce.peek_next()

    def generate_keypair(self) -> str:
        """Generate ephemeral X25519 keypair. Returns base64-encoded public key string."""
        self._private_key, self._local_public = _crypto.generate_ephemeral_keypair()
        self._established = False
        self._session_key = None
        self._session_id = None
        self._nonce.reset()
        return base64.b64encode(self._local_public).decode("ascii")

    def establish(self, sequencer_pubkey_b64: str, session_id: int) -> None:
        """
        Complete ECDH handshake: derive session key from sequencer's public key.
        Called after receiving session_established from the server.
        """
        if self._private_key is None or self._local_public is None:
            raise RuntimeError("Must call generate_keypair() before establish()")

        remote_public = base64.b64decode(sequencer_pubkey_b64)
        if len(remote_public) != 32:
            raise ValueError(f"Sequencer public key must be 32 bytes, got {len(remote_public)}")

        self._session_key = _crypto.derive_session_key(
            self._private_key, self._local_public, remote_public
        )
        self._session_id = session_id
        self._nonce.reset()
        self._established = True
        self._private_key = None

    def encrypt_order(self, aad: bytes, plaintext: bytes) -> tuple[int, bytes]:
        """
        Encrypt an order payload. Returns (nonce_counter, ciphertext).
        aad = protobuf-encoded OrderHeader bytes.
        """
        if not self._established:
            raise RuntimeError("Session not established")
        nonce_counter = self._nonce.advance()
        ct = _crypto.encrypt(self._session_key, nonce_counter, self._session_id, aad, plaintext)
        return nonce_counter, ct

    def decrypt_push(self, nonce_counter: int, aad: bytes, ciphertext: bytes) -> bytes:
        """
        Decrypt an encrypted_push from the sequencer.
        aad = protobuf-encoded ResponseHeader bytes.
        """
        if not self._established:
            raise RuntimeError("Session not established")
        pt = _crypto.decrypt(self._session_key, nonce_counter, self._session_id, aad, ciphertext)
        self._nonce.commit_recv(nonce_counter)
        return pt

    def reset(self) -> None:
        """Reset session state (for reconnect or rekey)."""
        self._private_key = None
        self._local_public = None
        self._session_key = None
        self._session_id = None
        self._nonce.reset()
        self._established = False
