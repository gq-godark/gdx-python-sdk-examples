"""GodarkClient -- the main entry point for the GoDark Python Trading SDK."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

TStream = TypeVar("TStream")

from . import _crypto, _identity, _proto  # noqa: F401 (_crypto registers protobuf codecs)
from ._session import CryptoSession
from ._symbols import load_default_symbol_map
from ._transport import EdgeTransport, TransportConfig
from .enums import _RESPONSE_MESSAGE_TYPE_TO_PROTO, OrderType, Side, TimeInForce
from .errors import (
    AuthenticationError,
    ConnectionError,
    EncryptionError,
    GodarkError,
    OrderError,
    SessionError,
)
from .order_error_code import make_order_error_from_code, make_order_error_from_json
from .types import (
    BalanceUpdate,
    FundingRateUpdate,
    MarginAlert,
    OrderAck,
    OrderUpdate,
    PositionUpdate,
    PositionsSnapshot,
    SettlementUpdate,
    SystemHealthUpdate,
    UnknownSequencerPush,
)

logger = logging.getLogger("godark")

_GCM_TAG_LEN = 16
_REQUEST_TYPE_MAP = {"place": "place", "cancel": "cancel", "modify": "modify"}

# Production WebSocket origin (GodarkClient appends `/ws/v1`).
_DEFAULT_EDGE_BASE_URL = "wss://api.godark-dex.com"


def _resolve_edge_base_url(explicit: str | None) -> str:
    """
    Resolve edge base URL: constructor arg wins, then env, then production default.

    Reads ``GODARK_EDGE_URL`` or ``GDX_EDGE_URL`` (first non-empty) so localnet /
    scripts can set the host without passing ``base_url`` in code.
    """
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip()
    for key in ("GODARK_EDGE_URL", "GDX_EDGE_URL"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return _DEFAULT_EDGE_BASE_URL


def _resolve_user_uuid(explicit: str | None) -> str | None:
    """Resolve user_uuid: constructor arg wins, then env vars."""
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip()
    for key in ("GODARK_USER_UUID", "GDX_USER_UUID"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return None


def _ws_url(base_url: str) -> str:
    """Return the canonical WebSocket URL ending in ``/ws/v1``.

    - If ``base_url`` already ends with ``/ws/v1``, returns it unchanged.
    - If it ends with the legacy ``/ws`` suffix, upgrades it to ``/ws/v1``.
    - Otherwise, appends ``/ws/v1`` to the (slash-stripped) base.
    """
    url = base_url.rstrip("/")
    if url.endswith("/ws/v1"):
        return url
    if url.endswith("/ws"):
        return url + "/v1"
    return url + "/ws/v1"


def _new_correlation_id() -> bytes:
    return uuid.uuid4().bytes


def _timestamp_ns() -> int:
    return int(time.time() * 1_000_000_000)


def _coerce_numeric_error_code(value: Any) -> int | None:
    """If ``value`` is a protobuf-style numeric ack code, return it."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


class GodarkClient:
    """
    Async trading client for the GoDark DEX.

    Handles API-key authentication, ECDH session negotiation, AES-256-GCM
    encrypted order flow, and real-time order/position streaming.

    Parameters:
        api_key: Legacy single opaque API key.
        api_key_id: Key-pair public ID (use with ``api_secret``).
        api_secret: Key-pair secret (use with ``api_key_id``).
        base_url: Edge WebSocket origin (host only, e.g.
            ``wss://api.godarkdex.com``). The client appends ``/ws/v1`` to
            produce the final upgrade URL. Defaults to production; override
            with arg or ``GODARK_EDGE_URL`` / ``GDX_EDGE_URL`` env vars.
        user_uuid: Fallback user UUID when the edge auth response omits it
            (e.g. local edge). Also reads ``GODARK_USER_UUID`` / ``GDX_USER_UUID``.
        auto_reconnect: Automatically reconnect on disconnect.
        symbol_map: Custom symbol-name-to-id mapping.
        transport: Low-level transport config (TLS, timeouts, etc.).
        stream_buffer_size: Max buffered order/position updates.

    Usage::

        async with GodarkClient(api_key_id="gdk_…", api_secret="…") as client:
            ...

        # Local edge (no user_uuid in auth response):
        async with GodarkClient(
            api_key="test-key-1",
            base_url="ws://localhost:4000",
            user_uuid="00000000-0000-4000-8000-000000000001",
        ) as client:
            ack = await client.place_order(
                symbol="BTC-USDC-PERP", side="BUY", order_type="LIMIT",
                price=67500.0, quantity=0.1,
            )
            print(ack.order_id)
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_key_id: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        user_uuid: str | None = None,
        auto_reconnect: bool = True,
        symbol_map: dict[str, int] | None = None,
        transport: TransportConfig | None = None,
        stream_buffer_size: int = 256,
    ):
        if api_key_id is not None or api_secret is not None:
            if api_key_id is None or api_secret is None:
                raise ValueError("api_key_id and api_secret must be provided together")
            if api_key is not None:
                raise ValueError("use either api_key or (api_key_id, api_secret), not both")
            self._auth_token = f"{api_key_id}:{api_secret}"
        elif api_key is not None:
            self._auth_token = api_key
        else:
            raise ValueError("provide api_key or both api_key_id and api_secret")

        self._base_url = _resolve_edge_base_url(base_url)
        self._config_user_uuid = _resolve_user_uuid(user_uuid)
        self._auto_reconnect = auto_reconnect
        self._symbol_map = dict(symbol_map) if symbol_map is not None else load_default_symbol_map()
        self._transport_config = transport

        if stream_buffer_size < 1:
            raise ValueError("stream_buffer_size must be >= 1")

        self._transport = EdgeTransport(_ws_url(self._base_url), self._transport_config)
        self._session = CryptoSession()
        self._user_uuid: str | None = None
        self._account_id: str | None = None
        self._login_session_id: str | None = None
        self._token_expires_at: str | None = None
        self._cancel_on_disconnect = False
        self._connected = False

        self._desired_channels: set[str] = set()
        self._order_queue: asyncio.Queue[OrderUpdate] = asyncio.Queue(maxsize=stream_buffer_size)
        self._position_queue: asyncio.Queue[PositionUpdate] = asyncio.Queue(
            maxsize=stream_buffer_size
        )
        self._positions_snapshot_queue: asyncio.Queue[PositionsSnapshot] = asyncio.Queue(
            maxsize=stream_buffer_size
        )
        self._system_health_queue: asyncio.Queue[SystemHealthUpdate] = asyncio.Queue(
            maxsize=stream_buffer_size
        )
        self._balance_queue: asyncio.Queue[BalanceUpdate] = asyncio.Queue(
            maxsize=stream_buffer_size
        )
        self._margin_alert_queue: asyncio.Queue[MarginAlert] = asyncio.Queue(
            maxsize=stream_buffer_size
        )
        self._funding_rate_queue: asyncio.Queue[FundingRateUpdate] = asyncio.Queue(
            maxsize=stream_buffer_size
        )
        self._settlement_queue: asyncio.Queue[SettlementUpdate] = asyncio.Queue(
            maxsize=stream_buffer_size
        )
        self._order_callbacks: list[Callable[[OrderUpdate], None]] = []
        self._position_callbacks: list[Callable[[PositionUpdate], None]] = []
        self._positions_snapshot_callbacks: list[Callable[[PositionsSnapshot], None]] = []
        self._system_health_callbacks: list[Callable[[SystemHealthUpdate], None]] = []
        self._balance_callbacks: list[Callable[[BalanceUpdate], None]] = []
        self._margin_alert_callbacks: list[Callable[[MarginAlert], None]] = []
        self._funding_rate_callbacks: list[Callable[[FundingRateUpdate], None]] = []
        self._settlement_callbacks: list[Callable[[SettlementUpdate], None]] = []
        self._reconnect_callbacks: list[Callable[[], None]] = []
        self._error_callbacks: list[Callable[[BaseException], None]] = []

        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_attempts = 0
        self._max_backoff = 15.0
        self._intentional_close = False

    @property
    def user_uuid(self) -> str | None:
        """User UUID from the edge after successful auth."""
        return self._user_uuid

    @property
    def account_id(self) -> str | None:
        """Docs ``op: login`` account identifier, when supplied by the edge."""
        return self._account_id

    @property
    def login_session_id(self) -> str | None:
        """Docs ``op: login`` session identifier, when supplied by the edge."""
        return self._login_session_id

    @property
    def token_expires_at(self) -> str | None:
        """Docs ``op: login`` token expiry timestamp, when supplied by the edge."""
        return self._token_expires_at

    @property
    def cancel_on_disconnect(self) -> bool:
        """Effective docs ``cancel_on_disconnect`` setting for this socket."""
        return self._cancel_on_disconnect

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect, authenticate, and establish ECDH session."""
        self._intentional_close = False

        await self._transport.connect()
        self._transport.on_encrypted_push = self._handle_encrypted_push
        self._transport.on_rekey_required = lambda msg: asyncio.create_task(self._handle_rekey(msg))
        self._transport.on_disconnect = self._on_transport_disconnect

        auth_result = await self._transport.authenticate(self._auth_token)
        if not auth_result.get("success"):
            await self._transport.disconnect()
            raise AuthenticationError(auth_result.get("error", "authentication failed"))

        uid = auth_result.get("user_uuid") or auth_result.get("user_id")
        if uid is None:
            uid = self._config_user_uuid
        if uid is None:
            await self._transport.disconnect()
            raise AuthenticationError(
                "authentication succeeded but user_uuid missing in auth_result "
                "and no fallback provided via constructor or "
                "GODARK_USER_UUID / GDX_USER_UUID env vars"
            )
        self._user_uuid = str(uid)
        self._account_id = (
            str(auth_result["account_id"]) if auth_result.get("account_id") is not None else None
        )
        self._login_session_id = (
            str(auth_result["session_id"]) if auth_result.get("session_id") is not None else None
        )
        self._token_expires_at = (
            str(auth_result["token_expires_at"])
            if auth_result.get("token_expires_at") is not None
            else None
        )
        self._cancel_on_disconnect = bool(auth_result.get("cancel_on_disconnect", False))

        await self._setup_ecdh_session()
        self._connected = True
        self._reconnect_attempts = 0
        logger.info("GodarkClient connected and authenticated")

    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        self._intentional_close = True
        self._connected = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self._transport.disconnect()
        self._session.reset()

    async def logout(self) -> None:
        """Send docs ``op: logout`` when available, then close the WebSocket."""
        self._intentional_close = True
        try:
            if self._connected and self._transport.use_docs_wire:
                await self._transport.send_command(
                    {"id": str(uuid.uuid4()), "op": "logout", "args": {}}
                )
        finally:
            await self.disconnect()

    async def __aenter__(self) -> GodarkClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        side: str | Side,
        order_type: str | OrderType,
        quantity: float,
        price: float | None = None,
        time_in_force: str | TimeInForce = "GTC",
        aon: bool = False,
        min_fill_size: float | None = None,
        expiry_time: int | None = None,
    ) -> OrderAck:
        """Place an order. Returns OrderAck on success, raises OrderError on rejection."""
        self._ensure_ready()
        symbol_id = self._resolve_symbol(symbol)
        side_str = side.value if isinstance(side, Side) else side
        otype_str = order_type.value if isinstance(order_type, OrderType) else order_type
        tif_str = time_in_force.value if isinstance(time_in_force, TimeInForce) else time_in_force
        corr_id = _new_correlation_id()

        plaintext = _proto.build_place_order_proto(
            symbol_id=symbol_id,
            side=side_str,
            order_type=otype_str,
            quantity=quantity,
            user_uuid=self._user_uuid_bytes(),
            price=price,
            time_in_force=tif_str,
            aon=aon,
            min_fill_size=min_fill_size,
            expiry_time=expiry_time,
            correlation_id_bytes=corr_id,
            timestamp=_timestamp_ns(),
        )

        return await self._send_encrypted_order("place", symbol_id, plaintext, corr_id)

    async def cancel_order(self, order_id: str, symbol: str = "BTC-USDC-PERP") -> OrderAck:
        """Cancel an order by ID."""
        self._ensure_ready()
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()

        plaintext = _proto.build_cancel_order_proto(
            order_id=int(order_id),
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            correlation_id_bytes=corr_id,
        )

        return await self._send_encrypted_order("cancel", symbol_id, plaintext, corr_id)

    async def modify_order(
        self,
        order_id: str,
        symbol: str = "BTC-USDC-PERP",
        new_price: float | None = None,
        new_quantity: float | None = None,
    ) -> OrderAck:
        """Modify an existing order's price and/or quantity."""
        self._ensure_ready()
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()

        plaintext = _proto.build_modify_order_proto(
            order_id=int(order_id),
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            new_price=new_price,
            new_quantity=new_quantity,
            correlation_id_bytes=corr_id,
        )

        return await self._send_encrypted_order("modify", symbol_id, plaintext, corr_id)

    # ------------------------------------------------------------------
    # Subscriptions & streaming
    # ------------------------------------------------------------------

    async def subscribe(
        self, channels: tuple[str, ...] | list[str] = ("orders", "positions")
    ) -> None:
        """Subscribe to order and/or position update channels."""
        self._ensure_ready()
        ch_list = list(channels)
        for c in ch_list:
            self._desired_channels.add(c)
        await self._transport.send_subscribe(ch_list)

    async def unsubscribe(
        self, channels: tuple[str, ...] | list[str] = ("orders", "positions")
    ) -> None:
        """Unsubscribe from channels."""
        ch_list = list(channels)
        for c in ch_list:
            self._desired_channels.discard(c)
        if self._transport.is_connected:
            await self._transport.send_subscribe(ch_list, op="unsubscribe")

    async def order_updates(self) -> AsyncIterator[OrderUpdate]:
        """Async iterator yielding order updates as they arrive."""
        async for u in self._queue_iter(self._order_queue):
            yield u

    async def position_updates(self) -> AsyncIterator[PositionUpdate]:
        """Async iterator yielding position updates as they arrive."""
        async for u in self._queue_iter(self._position_queue):
            yield u

    async def _queue_iter(self, queue: asyncio.Queue[TStream]) -> AsyncIterator[TStream]:
        while self._connected or not queue.empty():
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield item
            except asyncio.TimeoutError:
                continue

    def on_order_update(self, callback: Callable[[OrderUpdate], None]) -> None:
        """Register a callback for order updates."""
        self._order_callbacks.append(callback)

    def on_position_update(self, callback: Callable[[PositionUpdate], None]) -> None:
        """Register a callback for position updates."""
        self._position_callbacks.append(callback)

    def on_positions_snapshot(self, callback: Callable[[PositionsSnapshot], None]) -> None:
        """Register for full positions batches (initial / periodic / event)."""
        self._positions_snapshot_callbacks.append(callback)

    def on_system_health(self, callback: Callable[[SystemHealthUpdate], None]) -> None:
        """Register for sequencer / MPC cluster health pulses."""
        self._system_health_callbacks.append(callback)

    def on_balance_update(self, callback: Callable[[BalanceUpdate], None]) -> None:
        """Register for shielded balance updates."""
        self._balance_callbacks.append(callback)

    def on_margin_alert(self, callback: Callable[[MarginAlert], None]) -> None:
        """Register for margin-tier transitions."""
        self._margin_alert_callbacks.append(callback)

    def on_funding_rate_update(self, callback: Callable[[FundingRateUpdate], None]) -> None:
        """Register for per-symbol funding rate ticks."""
        self._funding_rate_callbacks.append(callback)

    def on_settlement_update(self, callback: Callable[[SettlementUpdate], None]) -> None:
        """Register for settlement-batch lifecycle updates."""
        self._settlement_callbacks.append(callback)

    async def positions_snapshots(self) -> AsyncIterator[PositionsSnapshot]:
        """Iterate positions snapshot batches."""
        async for u in self._queue_iter(self._positions_snapshot_queue):
            yield u

    async def system_health_updates(self) -> AsyncIterator[SystemHealthUpdate]:
        async for u in self._queue_iter(self._system_health_queue):
            yield u

    async def balance_updates(self) -> AsyncIterator[BalanceUpdate]:
        async for u in self._queue_iter(self._balance_queue):
            yield u

    async def margin_alerts(self) -> AsyncIterator[MarginAlert]:
        async for u in self._queue_iter(self._margin_alert_queue):
            yield u

    async def funding_rate_updates(self) -> AsyncIterator[FundingRateUpdate]:
        async for u in self._queue_iter(self._funding_rate_queue):
            yield u

    async def settlement_updates(self) -> AsyncIterator[SettlementUpdate]:
        async for u in self._queue_iter(self._settlement_queue):
            yield u

    def on_reconnect(self, callback: Callable[[], None]) -> None:
        """Register a callback for reconnection events."""
        self._reconnect_callbacks.append(callback)

    def on_error(self, callback: Callable[[BaseException], None]) -> None:
        """Register a callback for session / encryption / push-parse errors (non-fatal)."""
        self._error_callbacks.append(callback)

    def _emit_error(self, err: BaseException) -> None:
        for cb in self._error_callbacks:
            try:
                cb(err)
            except Exception:
                logger.debug("on_error callback raised", exc_info=True)

    def _bounded_put(self, queue: asyncio.Queue, item: Any) -> None:  # type: ignore[type-arg]
        """Enqueue item; if full, drop the oldest entry (head) first."""
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            logger.warning("Stream buffer full (maxsize=%d), oldest item dropped", queue.maxsize)
        queue.put_nowait(item)

    # ------------------------------------------------------------------
    # Internals: ECDH session
    # ------------------------------------------------------------------

    async def _setup_ecdh_session(self) -> None:
        client_pk_b64 = self._session.generate_keypair()

        loop = asyncio.get_running_loop()
        session_future: asyncio.Future = loop.create_future()

        def _on_established(msg: dict):
            if not session_future.done():
                session_future.set_result(msg)

        prev = self._transport.on_session_established
        self._transport.on_session_established = _on_established

        if self._transport.use_docs_wire:
            await self._transport.send_json(
                {
                    "id": str(uuid.uuid4()),
                    "op": "session.setup",
                    "args": {"client_ecdh_pubkey": client_pk_b64},
                }
            )
        else:
            await self._transport.send_json(
                {
                    "type": "session_setup",
                    "data": {
                        "user_uuid": self._user_uuid,
                        "client_ecdh_pubkey": client_pk_b64,
                    },
                }
            )

        try:
            result = await asyncio.wait_for(session_future, timeout=10.0)
        except asyncio.TimeoutError:
            raise SessionError("ECDH session setup timed out") from None
        finally:
            self._transport.on_session_established = prev

        if result.get("type") == "error":
            raise SessionError(result.get("message", "session setup failed"))

        seq_pk_b64 = result.get("sequencer_ecdh_pubkey", "")
        session_id = result.get("session_id")
        if isinstance(session_id, str):
            session_id = int(session_id)

        self._session.establish(seq_pk_b64, session_id)
        logger.info("ECDH session established (session_id=%s)", session_id)

    # ------------------------------------------------------------------
    # Internals: encrypted order pipeline
    # ------------------------------------------------------------------

    async def _send_encrypted_order(
        self,
        request_type: str,
        symbol_id: int,
        plaintext: bytes,
        correlation_id: bytes = b"",
    ) -> OrderAck:
        """
        Encrypt and send one trading command, awaiting its ack.

        The transport serializes commands so **only one encrypted command may be
        in flight at a time** (see :class:`~godark._transport.EdgeTransport`).
        Concurrent calls to ``place_order`` / ``cancel_order`` / ``modify_order``
        will wait on the same transport lock; do not expect parallel acks.
        """
        body_length = len(plaintext) + _GCM_TAG_LEN
        nonce_counter = self._session.next_nonce

        aad = _proto.build_order_header_aad(
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            request_type_str=request_type,
            nonce=nonce_counter,
            body_length=body_length,
            correlation_id=correlation_id,
        )

        try:
            actual_nonce, ciphertext = self._session.encrypt_order(aad, plaintext)
        except Exception as e:
            raise EncryptionError(f"Failed to encrypt order: {e}") from e

        body_b64 = base64.b64encode(ciphertext).decode("ascii")
        corr_id_str = _identity.bytes_to_uuid(correlation_id) if len(correlation_id) == 16 else ""
        header_obj = {
            "symbol_id": symbol_id,
            "request_type": request_type,
            "nonce": actual_nonce,
            "body_length": body_length,
            "correlation_id": corr_id_str,
        }
        if self._transport.use_docs_wire:
            op_map = {"place": "order.place", "cancel": "order.cancel", "modify": "order.modify"}
            payload = {
                "id": str(uuid.uuid4()),
                "op": op_map[request_type],
                "args": {"header": header_obj, "ciphertext": body_b64},
            }
        else:
            payload = {
                "type": "encrypted_order",
                "data": {
                    "header": header_obj,
                    "encrypted_body": body_b64,
                },
            }

        response = await self._transport.send_command(payload)
        return self._parse_order_response(response)

    def _parse_order_response(self, msg: dict) -> OrderAck:
        msg_type = msg.get("type")

        if msg_type == "error":
            raise make_order_error_from_json(msg.get("message"), msg.get("error_code"))

        if msg_type == "ack":
            if not msg.get("success"):
                raw_code = msg.get("error_code")
                numeric = _coerce_numeric_error_code(raw_code)
                if numeric is not None:
                    raise make_order_error_from_code(numeric)
                raise make_order_error_from_json(
                    msg.get("error", "order rejected"),
                    str(raw_code) if raw_code is not None else None,
                )
            return OrderAck(
                order_id=str(msg.get("order_id", "")),
                success=True,
                sequence=str(msg.get("sequence", "")),
            )

        if msg_type == "encrypted_push":
            return self._decrypt_ack_push(msg)

        raise OrderError(f"Unexpected response type: {msg_type}")

    def _decrypt_ack_push(self, msg: dict) -> OrderAck:
        ct_b64 = msg.get("encrypted_body", "")
        ct = base64.b64decode(ct_b64)
        nonce = msg.get("nonce", 0)
        user_uuid_bytes = self._user_uuid_bytes()
        message_type = msg.get("message_type", "ack")
        fencing_epoch = msg.get("fencing_epoch", 0)

        aad = _proto.build_response_header_aad(
            user_uuid=user_uuid_bytes,
            message_type_str=message_type,
            body_length=len(ct),
            nonce=nonce,
            fencing_epoch=fencing_epoch,
        )

        try:
            plaintext = self._session.decrypt_push(nonce, aad, ct)
        except Exception as e:
            raise EncryptionError(f"Failed to decrypt ack: {e}") from e

        ack_dict = _proto.parse_node_response(plaintext)
        if ack_dict.get("type") != "ack":
            raise OrderError(f"Expected ack, got {ack_dict.get('type')}")

        if not ack_dict.get("success"):
            raw_code = ack_dict.get("error_code")
            numeric = _coerce_numeric_error_code(raw_code)
            if numeric is not None:
                raise make_order_error_from_code(numeric)
            ec = raw_code if raw_code is not None else ""
            raise make_order_error_from_json(
                "order rejected",
                str(ec) if ec != "" else None,
            )

        return OrderAck(
            order_id=str(ack_dict.get("order_id", "")),
            success=True,
            sequence=str(ack_dict.get("sequence", "")),
        )

    # ------------------------------------------------------------------
    # Internals: push message handlers
    # ------------------------------------------------------------------

    def _handle_encrypted_push(self, msg: dict) -> None:
        ct_b64 = msg.get("encrypted_body", "")
        ct = base64.b64decode(ct_b64)
        nonce = msg.get("nonce", 0)
        user_uuid_bytes = self._user_uuid_bytes()
        message_type = msg.get("message_type", "")
        fencing_epoch = msg.get("fencing_epoch", 0)

        if message_type == "ack":
            self._transport.resolve_command(msg)
            return

        # Skip push types we don't have an AAD enum value for. The server
        # may legitimately add new message types ahead of the SDK; logging
        # at DEBUG and returning is the right behaviour. Without this guard
        # an unknown type propagated a KeyError out of build_response_header_aad
        # and silently killed the recv loop (= killed the WebSocket).
        if message_type not in _RESPONSE_MESSAGE_TYPE_TO_PROTO:
            logger.debug("Ignoring unknown encrypted push message_type=%r", message_type)
            return

        aad = _proto.build_response_header_aad(
            user_uuid=user_uuid_bytes,
            message_type_str=message_type,
            body_length=len(ct),
            nonce=nonce,
            fencing_epoch=fencing_epoch,
        )

        try:
            plaintext = self._session.decrypt_push(nonce, aad, ct)
        except Exception as e:
            err = EncryptionError(f"Failed to decrypt push: {e}")
            err.__cause__ = e
            logger.error("Failed to decrypt push: %s", e)
            self._emit_error(err)
            return

        try:
            parsed = _proto.parse_sequencer_to_edge_message(plaintext)
        except Exception as e:
            err = GodarkError(f"Failed to parse encrypted push body: {e}")
            err.__cause__ = e
            logger.error("Failed to parse encrypted push body: %s", e)
            self._emit_error(err)
            return

        self._dispatch_sequencer_push(parsed)

    def _dispatch_sequencer_push(self, parsed: _proto.SequencerPush) -> None:
        """Route decrypted ``SequencerToEdgeMessage`` inner variants to queues + callbacks."""
        if isinstance(parsed, OrderUpdate):
            self._bounded_put(self._order_queue, parsed)
            for cb in self._order_callbacks:
                with contextlib.suppress(Exception):
                    cb(parsed)
            return

        if isinstance(parsed, PositionUpdate):
            self._bounded_put(self._position_queue, parsed)
            for cb in self._position_callbacks:
                with contextlib.suppress(Exception):
                    cb(parsed)
            return

        if isinstance(parsed, PositionsSnapshot):
            self._bounded_put(self._positions_snapshot_queue, parsed)
            for cb in self._positions_snapshot_callbacks:
                with contextlib.suppress(Exception):
                    cb(parsed)
            return

        if isinstance(parsed, SystemHealthUpdate):
            self._bounded_put(self._system_health_queue, parsed)
            for cb in self._system_health_callbacks:
                with contextlib.suppress(Exception):
                    cb(parsed)
            return

        if isinstance(parsed, BalanceUpdate):
            self._bounded_put(self._balance_queue, parsed)
            for cb in self._balance_callbacks:
                with contextlib.suppress(Exception):
                    cb(parsed)
            return

        if isinstance(parsed, MarginAlert):
            self._bounded_put(self._margin_alert_queue, parsed)
            for cb in self._margin_alert_callbacks:
                with contextlib.suppress(Exception):
                    cb(parsed)
            return

        if isinstance(parsed, FundingRateUpdate):
            self._bounded_put(self._funding_rate_queue, parsed)
            for cb in self._funding_rate_callbacks:
                with contextlib.suppress(Exception):
                    cb(parsed)
            return

        if isinstance(parsed, SettlementUpdate):
            self._bounded_put(self._settlement_queue, parsed)
            for cb in self._settlement_callbacks:
                with contextlib.suppress(Exception):
                    cb(parsed)
            return

        if isinstance(parsed, UnknownSequencerPush):
            logger.debug(
                "Ignoring sequencer push with unknown or empty inner (oneof=%r)",
                parsed.oneof_field,
            )

    # ------------------------------------------------------------------
    # Internals: reconnect & rekey
    # ------------------------------------------------------------------

    async def _handle_rekey(self, msg: dict) -> None:
        logger.info("Rekey required, re-negotiating ECDH session")
        try:
            self._session.reset()
            await self._setup_ecdh_session()
        except Exception as e:
            if isinstance(e, SessionError):
                err: SessionError = e
            else:
                err = SessionError(f"Rekey failed: {e}")
                err.__cause__ = e
            logger.error("Rekey failed: %s", e)
            self._emit_error(err)

    def _on_transport_disconnect(self) -> None:
        self._connected = False
        if self._intentional_close or not self._auto_reconnect:
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while self._auto_reconnect and not self._intentional_close:
            delay = min(1.0 * (2**self._reconnect_attempts), self._max_backoff)
            self._reconnect_attempts += 1
            logger.info("Reconnecting in %.1fs (attempt %d)", delay, self._reconnect_attempts)
            await asyncio.sleep(delay)

            try:
                self._transport = EdgeTransport(_ws_url(self._base_url), self._transport_config)
                self._session.reset()
                await self.connect()

                if self._desired_channels:
                    await self.subscribe(list(self._desired_channels))

                for cb in self._reconnect_callbacks:
                    with contextlib.suppress(Exception):
                        cb()

                logger.info("Reconnected successfully")
                return
            except Exception as e:
                logger.warning("Reconnect failed: %s", e)

    # ------------------------------------------------------------------
    # Internals: helpers
    # ------------------------------------------------------------------

    def _ensure_ready(self) -> None:
        if not self._connected:
            raise ConnectionError("Not connected")
        if self._user_uuid is None:
            raise ConnectionError("Not authenticated")
        if not self._session.is_established:
            raise SessionError("ECDH session not established")

    @staticmethod
    def _parse_user_uuid_bytes(msg: dict) -> bytes:
        """Extract user UUID bytes from a push JSON message."""
        raw = msg.get("user_uuid") or msg.get("user_id")
        if isinstance(raw, str):
            try:
                return _identity.uuid_to_bytes(raw)
            except ValueError:
                pass
        return b"\x00" * _identity.USER_UUID_LEN

    def _user_uuid_bytes(self) -> bytes:
        """Return current user UUID as 16 raw bytes for protobuf fields."""
        if self._user_uuid is None:
            return b"\x00" * _identity.USER_UUID_LEN
        try:
            return _identity.uuid_to_bytes(self._user_uuid)
        except (ValueError, AttributeError):
            return b"\x00" * _identity.USER_UUID_LEN

    def _resolve_symbol(self, symbol: str) -> int:
        sid = self._symbol_map.get(symbol)
        if sid is None:
            raise ValueError(f"Unknown symbol '{symbol}'. Known: {list(self._symbol_map.keys())}")
        return sid
