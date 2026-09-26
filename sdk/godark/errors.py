from __future__ import annotations


class GodarkError(Exception):
    """Base error for all SDK errors."""


class AuthenticationError(GodarkError):
    """API key auth failed."""


class SessionError(GodarkError):
    """ECDH session setup or rekey failed."""


class OrderError(GodarkError):
    """Order was rejected by the sequencer."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        user_message: str | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.user_message = user_message


class ConnectionError(GodarkError):  # noqa: A001
    """WebSocket transport failure."""


class EncryptionError(GodarkError):
    """AES-GCM encryption or decryption failed."""


class TimeoutError(GodarkError):  # noqa: A001
    """Command timed out waiting for response."""
