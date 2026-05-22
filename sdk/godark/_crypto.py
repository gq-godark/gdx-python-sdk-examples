"""X25519 ECDH key exchange + AES-256-GCM encryption for gdx-edge E2E protocol."""

import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HKDF_INFO = b"gdx-e2e-session-key-v1"
GCM_TAG_LEN = 16


def generate_ephemeral_keypair() -> tuple[X25519PrivateKey, bytes]:
    """Generate an ephemeral X25519 keypair. Returns (private_key, public_key_bytes_32)."""
    private = X25519PrivateKey.generate()
    public_bytes = private.public_key().public_bytes_raw()
    return private, public_bytes


def derive_session_key(
    private_key: X25519PrivateKey, local_public: bytes, remote_public: bytes
) -> bytes:
    """
    Derive a 32-byte AES session key from X25519 ECDH + HKDF-SHA256.

    HKDF salt = 64 bytes: min(local_pub, remote_pub) || max(local_pub, remote_pub)
    (byte-lexicographic comparison, matching the reference ECDH wire encoding)

    HKDF info = b"gdx-e2e-session-key-v1"
    """
    remote_key = X25519PublicKey.from_public_bytes(remote_public)
    shared_secret = private_key.exchange(remote_key)

    if shared_secret == b"\x00" * 32:
        raise ValueError("Weak public key: ECDH shared secret is all zeros")

    if local_public <= remote_public:
        salt = local_public + remote_public
    else:
        salt = remote_public + local_public

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=HKDF_INFO,
    )
    return hkdf.derive(shared_secret)


def build_gcm_nonce(session_id: int, nonce_counter: int) -> bytes:
    """
    Build 96-bit GCM nonce: session_id (64-bit BE) || nonce_counter (32-bit BE).
    nonce_counter must fit in u32.
    """
    if nonce_counter > 0xFFFFFFFF:
        raise OverflowError(f"Nonce counter {nonce_counter} exceeds u32 max")
    return struct.pack(">Q", session_id) + struct.pack(">I", nonce_counter)


def encrypt(key: bytes, nonce_counter: int, session_id: int, aad: bytes, plaintext: bytes) -> bytes:
    """
    Encrypt with AES-256-GCM. Returns ciphertext + 16-byte auth tag.
    Nonce = session_id(8B BE) || nonce_counter(4B BE).
    """
    aesgcm = AESGCM(key)
    nonce = build_gcm_nonce(session_id, nonce_counter)
    return aesgcm.encrypt(nonce, plaintext, aad)


def decrypt(
    key: bytes, nonce_counter: int, session_id: int, aad: bytes, ciphertext: bytes
) -> bytes:
    """
    Decrypt AES-256-GCM ciphertext (includes auth tag).
    Raises InvalidTag on wrong key/AAD/tampered data.
    """
    aesgcm = AESGCM(key)
    nonce = build_gcm_nonce(session_id, nonce_counter)
    return aesgcm.decrypt(nonce, ciphertext, aad)


class NonceTracker:
    """Monotonic send nonce counter + receive replay detection."""

    def __init__(self):
        self._send_counter: int = 0
        self._last_recv: int | None = None

    def peek_next(self) -> int:
        return self._send_counter

    def advance(self) -> int:
        n = self._send_counter
        if n > 0xFFFFFFFF:
            raise OverflowError("Send nonce counter exceeded u32 max")
        self._send_counter = n + 1
        return n

    def commit_recv(self, received: int) -> None:
        self._last_recv = received

    def reset(self) -> None:
        self._send_counter = 0
        self._last_recv = None
