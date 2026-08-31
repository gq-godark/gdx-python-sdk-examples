"""Low-level WebSocket transport for gdx-edge."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from ._wire import DecodedBinary, decode_binary_frame, encrypted_push_to_json
from .errors import TimeoutError as GdxTimeoutError

logger = logging.getLogger("godark.transport")


def _is_docs_reply(msg: dict[str, Any]) -> bool:
    if msg.get("type") is not None:
        return False
    if "op" not in msg or "code" not in msg:
        return False
    return isinstance(msg["code"], int)


def _normalize_inbound_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Map gdx-edge docs replies to legacy ``type`` / ``event`` frames."""
    if not _is_docs_reply(msg):
        return msg

    code = msg["code"]
    op = str(msg.get("op", ""))
    data = msg.get("data")
    message = msg.get("message")
    err_text = message if isinstance(message, str) else None

    if op == "pong" and code == 0:
        return {"type": "pong"}

    if op == "login":
        if code != 0:
            return {
                "type": "auth_result",
                "success": False,
                "error": err_text or "authentication failed",
            }
        if isinstance(data, dict):
            uid = data.get("user_uuid")
            return {
                "type": "auth_result",
                "success": True,
                "user_uuid": uid,
                "conn_id": data.get("conn_id"),
                "account_id": data.get("account_id"),
                "session_id": data.get("session_id"),
                "token_expires_at": data.get("token_expires_at"),
                "cancel_on_disconnect": data.get("cancel_on_disconnect", False),
            }
        return {"type": "auth_result", "success": False, "error": "invalid auth response"}

    if op in ("session.setup", "session_setup"):
        if code != 0:
            return {"type": "error", "message": err_text or "session setup failed"}
        if not isinstance(data, dict):
            return {"type": "error", "message": "invalid session response"}
        seq_pk = data.get("sequencer_ecdh_pubkey") or data.get("server_ecdh_pubkey", "")
        return {
            "type": "session_established",
            "sequencer_ecdh_pubkey": seq_pk,
            "session_id": data.get("session_id"),
        }

    if op in ("noise.handshake", "noise_handshake"):
        if code != 0:
            return {"type": "error", "message": err_text or "Noise handshake failed"}
        if not isinstance(data, dict):
            return {"type": "error", "message": "invalid Noise handshake response"}
        return {
            "type": "noise_handshake_reply",
            "conn_id": data.get("conn_id"),
            "message": data.get("message", ""),
            "established": bool(data.get("established", False)),
        }

    if op in ("subscribe", "unsubscribe"):
        if code != 0:
            ch = ""
            if isinstance(data, dict):
                ch = str(data.get("channel", ""))
            return {
                "event": "error",
                "message": err_text or "channel error",
                "channel": ch,
            }
        if isinstance(data, dict) and "channel" in data:
            return {"event": op, "channel": data["channel"]}
        return {"event": op}

    if op == "logout":
        if code != 0:
            return {"type": "error", "message": err_text or "logout failed"}
        return {"type": "ack", "success": True}

    if op in (
        "order.place",
        "order.cancel",
        "order.modify",
        "order.mass_quote",
        "order.batch_cancel",
        "order.batch_modify",
    ):
        wire_id = msg.get("id")
        if code != 0:
            return {
                "type": "error",
                "message": err_text or "order error",
                "wire_id": wire_id,
            }
        if not isinstance(data, dict):
            return {"type": "error", "message": "invalid order response"}
        # Encrypted acks (single-order "ack" or batch "*_ack") come back as a
        # ciphertext frame the client decrypts; surface them as a push.
        if data.get("message_type") and ("ciphertext" in data or "encrypted_body" in data):
            ciphertext = data.get("ciphertext", data.get("encrypted_body", ""))
            return {
                "type": "encrypted_push",
                "message_type": data.get("message_type"),
                "encrypted_body": ciphertext,
                "nonce": data.get("nonce", 0),
                "fencing_epoch": data.get("fencing_epoch", 0),
                "correlation_id": data.get("correlation_id"),
                "session_seq": data.get("session_seq"),
                "conn_id": data.get("conn_id"),
                "wire_id": wire_id,
            }
        return {
            "type": "ack",
            "success": data.get("success", True),
            "order_id": data.get("order_id"),
            "sequence": data.get("sequence"),
            "error": data.get("error"),
            "error_code": data.get("error_code"),
            "correlation_id": data.get("correlation_id"),
            "wire_id": wire_id,
        }

    if isinstance(data, dict) and data.get("event") == "rekey_required":
        return {"type": "rekey_required", "session_id": data.get("session_id")}

    if (
        isinstance(data, dict)
        and data.get("message_type")
        and ("ciphertext" in data or "encrypted_body" in data)
    ):
        ciphertext = data.get("ciphertext", data.get("encrypted_body", ""))
        return {
            "type": "encrypted_push",
            "message_type": data.get("message_type"),
            "encrypted_body": ciphertext,
            "nonce": data.get("nonce", 0),
            "fencing_epoch": data.get("fencing_epoch", 0),
            "correlation_id": data.get("correlation_id"),
            "session_seq": data.get("session_seq"),
            "conn_id": data.get("conn_id"),
        }

    return msg


@dataclass
class TransportConfig:
    """
    Optional WebSocket transport settings (TLS, proxy, headers, timeouts).

    ``ssl`` is passed to :func:`websockets.connect` (use a custom
    :class:`ssl.SSLContext` for client certs, CA bundles, etc.).
    ``wss://`` URIs default to the platform TLS context when ``ssl`` is omitted.

    When ``use_docs_wire`` is True (default), outbound frames use the public-docs
    envelope ``{id, op, args}`` and inbound ``{id, op, code, data?, message?}``
    replies are normalized to the legacy ``type`` / ``event`` shapes the rest of
    the client expects.
    """

    ssl: ssl.SSLContext | bool | None = None
    additional_headers: Mapping[str, str] | None = None
    proxy: str | bool | None = True
    open_timeout: float | None = None
    # Match Java/Go trading default: open_orders_snapshot frames grow with resting
    # order count and routinely exceed the historical websockets 64 KiB default.
    max_size: int | None = 8 * 1024 * 1024
    heartbeat_interval: float | None = None
    stale_timeout: float | None = None
    command_timeout: float | None = None
    use_docs_wire: bool = True


class EdgeTransport:
    """
    Async WebSocket transport for gdx-edge ``/ws/v1`` endpoint.

    Handles connection, JSON framing, heartbeat, and message dispatch.
    """

    HEARTBEAT_INTERVAL = 30.0
    STALE_TIMEOUT = 60.0
    COMMAND_TIMEOUT = 30.0

    def __init__(self, url: str, config: TransportConfig | None = None):
        self._url = url
        self._config = config or TransportConfig()
        self._heartbeat_interval: float = (
            self._config.heartbeat_interval
            if self._config.heartbeat_interval is not None
            else self.HEARTBEAT_INTERVAL
        )
        self._stale_timeout: float = (
            self._config.stale_timeout
            if self._config.stale_timeout is not None
            else self.STALE_TIMEOUT
        )
        self._command_timeout: float = (
            self._config.command_timeout
            if self._config.command_timeout is not None
            else self.COMMAND_TIMEOUT
        )
        self._ws: ClientConnection | None = None
        self._connected = False
        self._last_inbound: float = 0.0
        self._heartbeat_task: asyncio.Task | None = None
        self._recv_task: asyncio.Task | None = None

        # Command waiters.
        #
        # Correlation-keyed commands (encrypted trading ops) may be in flight
        # concurrently: each registers a future under its correlation id and
        # awaits it *without* holding a global lock, so throughput is bounded by
        # the server round-trip rather than one-command-at-a-time. Commands with
        # no correlation id (e.g. the noise handshake) fall back to the single
        # ``_cmd_future`` slot and are serialized via ``_cmd_lock``.
        self._cmd_future: asyncio.Future | None = None
        # Concurrent command waiters, keyed by header correlation id and by
        # wire id. Business rejects come back as a top-level error that carries
        # only the wire id (no correlation id), so both keys map to the same
        # future to guarantee the response finds its waiter.
        self._pending_by_correlation: dict[str, asyncio.Future] = {}
        self._pending_by_wire_id: dict[str, asyncio.Future] = {}
        self._cmd_lock = asyncio.Lock()
        self._use_docs_wire: bool = self._config.use_docs_wire
        self._hpke_setup_future: asyncio.Future | None = None

        # Subscription waiters
        self._sub_waiter: asyncio.Future | None = None
        self._sub_expected: int = 0
        self._sub_op: str = ""

        # Message handlers (set by client.py).
        # Order/position updates only arrive inside encrypted_push frames; the
        # cleartext order_update/position_update wire path was removed when the
        # edge moved to encrypt-everything for authenticated user streams.
        self.on_auth_result: Callable | None = None
        self.on_encrypted_push: Callable | None = None
        self.on_session_established: Callable | None = None
        self.on_rekey_required: Callable | None = None
        self.on_disconnect: Callable | None = None
        # Public market snapshots (funding_rate, volume, open_interest) on /ws/v1.
        self.on_public_message: Callable | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def use_docs_wire(self) -> bool:
        return self._use_docs_wire

    @property
    def command_timeout(self) -> float:
        return self._command_timeout

    def _new_wire_id(self) -> str:
        return str(uuid.uuid4())

    async def connect(self) -> None:
        """Open WebSocket connection."""
        self._ws = await self._open_connection()
        self._connected = True
        self._last_inbound = time.monotonic()
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Connected to %s", self._url)

    def _connect_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "max_size": self._config.max_size
            if self._config.max_size is not None
            else 8 * 1024 * 1024,
        }
        if self._config.ssl is not None:
            kw["ssl"] = self._config.ssl
        if self._config.additional_headers:
            kw["additional_headers"] = dict(self._config.additional_headers)
        if self._config.proxy is not True:
            kw["proxy"] = self._config.proxy
        if self._config.open_timeout is not None:
            kw["open_timeout"] = self._config.open_timeout
        return kw

    async def _open_connection(self) -> ClientConnection:
        return await websockets.connect(self._url, **self._connect_kwargs())

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._connected = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        self._reject_pending("disconnected")
        logger.info("Disconnected")

    async def send_json(self, obj: dict) -> None:
        """Send a JSON message."""
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(json.dumps(obj))

    async def send_binary(self, data: bytes) -> None:
        """Send a binary WebSocket frame."""
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.send(data)

    @staticmethod
    def _command_correlation_id(payload: dict) -> str:
        """Return the normalized correlation id carried by an outbound command.

        Encrypted trading ops stamp a 16-byte correlation id (hex) into the
        header. Returns "" when the payload has no correlation id (e.g. the
        noise handshake), which selects the serialized single-slot path.
        """
        header: Any = None
        args = payload.get("args")
        if isinstance(args, dict):
            header = args.get("header")
        if header is None:
            data = payload.get("data")
            if isinstance(data, dict):
                header = data.get("header")
        if isinstance(header, dict):
            corr = header.get("correlation_id")
            if isinstance(corr, str) and corr:
                return corr.lower()
        return ""

    async def send_command(
        self,
        payload: dict | None = None,
        *,
        prepare: Callable[[], dict] | None = None,
    ) -> dict:
        """
        Send a command and wait for its ack/error response.

        Either ``payload`` (a ready frame) or ``prepare`` (a callable that
        builds and returns the frame while the send lock is held) must be
        given. ``prepare`` lets callers keep nonce assignment / encryption
        atomic with the send so concurrent commands still hit the wire in
        nonce order.

        Commands carrying a correlation id (encrypted trading ops) may be in
        flight concurrently: the response is matched back by correlation id, so
        the send lock is released as soon as the frame is on the wire. Commands
        without a correlation id use the single ``_cmd_future`` slot and are
        serialized so their unkeyed responses cannot be confused.
        """
        loop = asyncio.get_running_loop()

        # Fast path: correlation id is knowable up front (payload given).
        # When ``prepare`` builds the frame under the lock we discover the
        # correlation id after building it.
        if payload is not None and prepare is None:
            corr = self._command_correlation_id(payload)
            if not corr:
                return await self._send_serialized(payload, loop)

        fut = loop.create_future()
        corr = ""
        wire_id = ""
        async with self._cmd_lock:
            if not self._ws:
                raise RuntimeError("Not connected")
            frame = prepare() if prepare is not None else payload
            assert frame is not None
            corr = self._command_correlation_id(frame)
            wire_id = frame.get("id") if isinstance(frame.get("id"), str) else ""
            if not corr:
                # No correlation id even after prepare(): fall back to the
                # serialized single slot, still under the same lock.
                self._cmd_future = fut
                try:
                    await self.send_json(frame)
                except Exception:
                    self._cmd_future = None
                    raise
            else:
                existing = self._pending_by_correlation.get(corr)
                if existing is not None and not existing.done():
                    existing.set_exception(RuntimeError("superseded by duplicate correlation id"))
                self._pending_by_correlation[corr] = fut
                if wire_id:
                    self._pending_by_wire_id[wire_id] = fut
                try:
                    await self.send_json(frame)
                except Exception:
                    self._pending_by_correlation.pop(corr, None)
                    if wire_id:
                        self._pending_by_wire_id.pop(wire_id, None)
                    raise

        try:
            return await asyncio.wait_for(fut, timeout=self._command_timeout)
        except asyncio.TimeoutError:
            raise GdxTimeoutError(f"Command timed out after {self._command_timeout:.0f}s") from None
        finally:
            if corr:
                self._pending_by_correlation.pop(corr, None)
                if wire_id:
                    self._pending_by_wire_id.pop(wire_id, None)
            elif self._cmd_future is fut:
                self._cmd_future = None

    async def send_hpke_setup(self, frame: bytes) -> dict:
        """Send an HPKE setup binary frame and await the setup reply."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._hpke_setup_future = fut
        try:
            await self.send_binary(frame)
            return await asyncio.wait_for(fut, timeout=self._command_timeout)
        except asyncio.TimeoutError:
            raise GdxTimeoutError("HPKE setup timed out") from None
        finally:
            if self._hpke_setup_future is fut:
                self._hpke_setup_future = None

    async def send_binary_command(
        self,
        *,
        prepare: Callable[[], bytes],
        correlation_id: str = "",
    ) -> dict:
        """Send a binary trading command and wait for its encrypted_push ack."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        corr = correlation_id.lower() if correlation_id else ""
        async with self._cmd_lock:
            if not self._ws:
                raise RuntimeError("Not connected")
            frame = prepare()
            if corr:
                existing = self._pending_by_correlation.get(corr)
                if existing is not None and not existing.done():
                    existing.set_exception(RuntimeError("superseded by duplicate correlation id"))
                self._pending_by_correlation[corr] = fut
            else:
                self._cmd_future = fut
            try:
                await self.send_binary(frame)
            except Exception:
                if corr:
                    self._pending_by_correlation.pop(corr, None)
                elif self._cmd_future is fut:
                    self._cmd_future = None
                raise
        try:
            return await asyncio.wait_for(fut, timeout=self._command_timeout)
        except asyncio.TimeoutError:
            raise GdxTimeoutError(f"Command timed out after {self._command_timeout:.0f}s") from None
        finally:
            if corr:
                self._pending_by_correlation.pop(corr, None)
            elif self._cmd_future is fut:
                self._cmd_future = None

    async def _send_serialized(self, payload: dict, loop: asyncio.AbstractEventLoop) -> dict:
        async with self._cmd_lock:
            if not self._ws:
                raise RuntimeError("Not connected")
            self._cmd_future = loop.create_future()
            await self.send_json(payload)
            try:
                result = await asyncio.wait_for(self._cmd_future, timeout=self._command_timeout)
            except asyncio.TimeoutError:
                self._cmd_future = None
                raise GdxTimeoutError(
                    f"Command timed out after {self._command_timeout:.0f}s"
                ) from None
            finally:
                self._cmd_future = None
            return result

    async def send_subscribe(self, channels: list[str], op: str = "subscribe") -> None:
        """Send subscribe/unsubscribe and wait for all channel acks."""
        async with self._cmd_lock:
            if not self._ws:
                raise RuntimeError("Not connected")
            loop = asyncio.get_running_loop()
            self._sub_waiter = loop.create_future()
            self._sub_expected = len(channels)
            self._sub_op = op
            sub_msg: dict[str, Any] = {
                "op": op,
                "args": [{"channel": c} for c in channels],
            }
            if self._use_docs_wire:
                sub_msg["id"] = self._new_wire_id()
            await self.send_json(sub_msg)
            try:
                await asyncio.wait_for(self._sub_waiter, timeout=self._command_timeout)
            except asyncio.TimeoutError:
                raise GdxTimeoutError(f"{op} timed out") from None
            finally:
                self._sub_waiter = None
                self._sub_expected = 0

    async def authenticate(self, api_key: str) -> dict:
        """Send auth message and await auth_result."""
        loop = asyncio.get_running_loop()
        auth_future: asyncio.Future = loop.create_future()

        prev_handler = self.on_auth_result

        def _handle_auth(msg: dict):
            if not auth_future.done():
                auth_future.set_result(msg)

        self.on_auth_result = _handle_auth

        if self._use_docs_wire:
            await self.send_json(
                {
                    "id": self._new_wire_id(),
                    "op": "login",
                    "args": {"token": api_key},
                }
            )
        else:
            await self.send_json({"type": "auth", "data": {"token": api_key}})
        try:
            result = await asyncio.wait_for(auth_future, timeout=self._command_timeout)
        except asyncio.TimeoutError:
            raise GdxTimeoutError("Auth timed out") from None
        finally:
            self.on_auth_result = prev_handler

        return result

    @staticmethod
    def _normalize_correlation_key(corr: Any) -> str:
        """Canonical hex key for correlation waiters.

        Outbound headers stamp 32-char hex; encrypted ack pushes often echo the
        same u128 as a decimal string. Normalize both to lowercase hex so
        ``resolve_command`` can match concurrent waiters.
        """
        if isinstance(corr, int):
            if corr < 0:
                return ""
            return corr.to_bytes(16, "big").hex()
        if not isinstance(corr, str) or not corr:
            return ""
        s = corr.strip().lower()
        if not s:
            return ""
        if s.isdigit():
            try:
                return int(s).to_bytes(16, "big").hex()
            except (OverflowError, ValueError):
                return s
        if len(s) == 32 and all(c in "0123456789abcdef" for c in s):
            return s
        return s

    def resolve_command(self, result: dict) -> bool:
        """Resolve the pending command future with the given result.

        When the result carries a correlation id it is matched back to the
        specific concurrent waiter registered by ``send_command``; otherwise it
        falls back to the single ``_cmd_future`` slot. Returns True if a
        pending future was resolved. Used by GodarkClient to route encrypted
        ack pushes back to the awaiting ``send_command`` call.
        """
        # Prefer the header correlation id; fall back to the wire id (business
        # rejects arrive as a top-level error carrying only the wire id).
        corr = self._normalize_correlation_key(result.get("correlation_id"))
        wire_id = result.get("wire_id")
        wire_id = wire_id if isinstance(wire_id, str) and wire_id else ""

        fut: asyncio.Future | None = None
        if corr:
            fut = self._pending_by_correlation.get(corr)
        if fut is None and wire_id:
            fut = self._pending_by_wire_id.get(wire_id)
        if fut is not None:
            self._forget_waiter(fut)
            if not fut.done():
                fut.set_result(result)
                return True

        if self._cmd_future and not self._cmd_future.done():
            self._cmd_future.set_result(result)
            return True
        return False

    def _forget_waiter(self, fut: asyncio.Future) -> None:
        """Drop a future from both correlation and wire-id waiter maps."""
        for key, val in list(self._pending_by_correlation.items()):
            if val is fut:
                self._pending_by_correlation.pop(key, None)
        for key, val in list(self._pending_by_wire_id.items()):
            if val is fut:
                self._pending_by_wire_id.pop(key, None)

    def _reject_pending(self, reason: str) -> None:
        """Reject any pending command/subscription futures."""
        for fut in list(self._pending_by_correlation.values()) + list(
            self._pending_by_wire_id.values()
        ):
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._pending_by_correlation.clear()
        self._pending_by_wire_id.clear()
        if self._cmd_future and not self._cmd_future.done():
            self._cmd_future.set_exception(RuntimeError(reason))
        self._cmd_future = None
        if self._hpke_setup_future and not self._hpke_setup_future.done():
            self._hpke_setup_future.set_exception(RuntimeError(reason))
        self._hpke_setup_future = None
        if self._sub_waiter and not self._sub_waiter.done():
            self._sub_waiter.set_exception(RuntimeError(reason))
        self._sub_waiter = None

    def _dispatch_binary(self, data: bytes) -> None:
        try:
            kind, payload = decode_binary_frame(data)
        except Exception as exc:
            logger.warning("binary frame decode failed: %s", exc)
            return
        if kind is DecodedBinary.HPKE_SETUP_REPLY:
            reply = payload
            msg = {
                "type": "hpke_setup_reply",
                "conn_id": reply.conn_id,
                "established": reply.established,
            }
            if self._hpke_setup_future and not self._hpke_setup_future.done():
                self._hpke_setup_future.set_result(msg)
            return
        if kind is DecodedBinary.ENCRYPTED_PUSH:
            msg = encrypted_push_to_json(payload)
            if msg and self.on_encrypted_push:
                self.on_encrypted_push(msg)
            return

    async def _recv_loop(self) -> None:
        """Background task: read messages from WebSocket and dispatch."""
        try:
            async for raw in self._ws:
                self._last_inbound = time.monotonic()
                if isinstance(raw, bytes):
                    self._dispatch_binary(raw)
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._dispatch(_normalize_inbound_message(msg))
        except websockets.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("recv_loop error: %s", e)
        finally:
            self._connected = False
            self._reject_pending("connection lost")
            if self.on_disconnect:
                try:
                    result = self.on_disconnect()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

    def _dispatch(self, msg: dict) -> None:
        """Route inbound message to the appropriate handler."""
        msg_type = msg.get("type")
        event = msg.get("event")

        if msg_type == "pong":
            return

        if msg_type == "auth_result":
            if self.on_auth_result:
                self.on_auth_result(msg)
            return

        if msg_type == "session_established":
            if self.on_session_established:
                self.on_session_established(msg)
            return

        if msg_type == "rekey_required":
            if self.on_rekey_required:
                self.on_rekey_required(msg)
            return

        if msg_type == "encrypted_push":
            if self.on_encrypted_push:
                self.on_encrypted_push(msg)
            return

        if msg_type == "hpke_setup_reply":
            self.resolve_command(msg)
            return

        if msg_type == "noise_handshake_reply":
            self.resolve_command(msg)
            return

        # Subscription acks (event-tagged, not type-tagged)
        if event in ("subscribe", "unsubscribe"):
            if self._sub_waiter and not self._sub_waiter.done() and event == self._sub_op:
                self._sub_expected -= 1
                if self._sub_expected <= 0:
                    self._sub_waiter.set_result(None)
            return

        if event == "error":
            if self._sub_waiter and not self._sub_waiter.done():
                self._sub_waiter.set_exception(RuntimeError(msg.get("message", "channel error")))
            return

        # ack / error responses for commands (routed by correlation id when
        # present, else to the single serialized slot).
        if msg_type in ("ack", "error"):
            self.resolve_command(msg)
            return

        if msg_type in ("funding_rate_snapshot", "volume_snapshot", "open_interest_snapshot"):
            if self.on_public_message:
                self.on_public_message(msg)
            return

    async def _heartbeat_loop(self) -> None:
        """Send periodic pings, detect stale connections."""
        try:
            while self._connected:
                await asyncio.sleep(self._heartbeat_interval)
                if not self._connected:
                    break
                elapsed = time.monotonic() - self._last_inbound
                if elapsed > self._stale_timeout:
                    logger.warning("Stale connection (%.1fs no inbound), closing", elapsed)
                    if self._ws:
                        await self._ws.close(4000, "heartbeat timeout")
                    break
                try:
                    if self._use_docs_wire:
                        await self.send_json(
                            {
                                "id": self._new_wire_id(),
                                "op": "ping",
                                "args": {},
                            }
                        )
                    else:
                        await self.send_json({"type": "ping"})
                except Exception:
                    break
        except asyncio.CancelledError:
            return
