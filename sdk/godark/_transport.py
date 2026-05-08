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

    if op in ("order.place", "order.cancel", "order.modify"):
        if code != 0:
            return {"type": "error", "message": err_text or "order error"}
        if not isinstance(data, dict):
            return {"type": "error", "message": "invalid order response"}
        if data.get("message_type") == "ack":
            ciphertext = data.get("ciphertext", data.get("encrypted_body", ""))
            return {
                "type": "encrypted_push",
                "message_type": "ack",
                "encrypted_body": ciphertext,
                "nonce": data.get("nonce", 0),
                "fencing_epoch": data.get("fencing_epoch", 0),
            }
        return {
            "type": "ack",
            "success": data.get("success", True),
            "order_id": data.get("order_id"),
            "sequence": data.get("sequence"),
            "error": data.get("error"),
            "error_code": data.get("error_code"),
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
    max_size: int | None = 65536
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

        # Command serialization: one pending command at a time
        self._cmd_future: asyncio.Future | None = None
        self._cmd_lock = asyncio.Lock()
        self._use_docs_wire: bool = self._config.use_docs_wire

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

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def use_docs_wire(self) -> bool:
        return self._use_docs_wire

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
            "max_size": self._config.max_size if self._config.max_size is not None else 65536,
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

    async def send_command(self, payload: dict) -> dict:
        """
        Send a command and wait for its ack/error response.
        Serializes commands so only one is in flight at a time.
        """
        async with self._cmd_lock:
            if not self._ws:
                raise RuntimeError("Not connected")
            loop = asyncio.get_running_loop()
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

    def resolve_command(self, result: dict) -> bool:
        """Resolve the pending command future with the given result.

        Returns True if a pending future was resolved, False otherwise.
        Used by GodarkClient to route encrypted ack pushes back to the
        awaiting send_command call without accessing private state.
        """
        if self._cmd_future and not self._cmd_future.done():
            self._cmd_future.set_result(result)
            return True
        return False

    def _reject_pending(self, reason: str) -> None:
        """Reject any pending command/subscription futures."""
        if self._cmd_future and not self._cmd_future.done():
            self._cmd_future.set_exception(RuntimeError(reason))
        self._cmd_future = None
        if self._sub_waiter and not self._sub_waiter.done():
            self._sub_waiter.set_exception(RuntimeError(reason))
        self._sub_waiter = None

    async def _recv_loop(self) -> None:
        """Background task: read messages from WebSocket and dispatch."""
        try:
            async for raw in self._ws:
                self._last_inbound = time.monotonic()
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

        # ack / error responses for commands
        if msg_type == "ack":
            if self._cmd_future and not self._cmd_future.done():
                self._cmd_future.set_result(msg)
            return

        if msg_type == "error":
            if self._cmd_future and not self._cmd_future.done():
                self._cmd_future.set_result(msg)
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
