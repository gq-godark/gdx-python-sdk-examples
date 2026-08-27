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
from enum import Enum
from typing import Any, Literal, TypeVar

from . import _identity, _proto
from ._hpke import pinned_sequencer_static_pub
from ._session import CryptoSession
from ._symbols import load_offline_symbol_map, load_symbol_map_from_edge
from ._transport import EdgeTransport, TransportConfig
from ._wire import build_order_header_proto, encode_encrypted_order, encrypted_order_request
from .enums import (
    _RESPONSE_MESSAGE_TYPE_TO_PROTO,
    OrderStatus,
    OrderType,
    OrderUpdateType,
    Side,
    TimeInForce,
)
from .errors import (
    AuthenticationError,
    ConnectionError,
    EncryptionError,
    GodarkError,
    OrderError,
    SessionError,
    TimeoutError,
)
from .order_error_code import make_order_error_from_json
from .types import (
    BalanceUpdate,
    BatchCancelAck,
    BatchCancelLegResult,
    BatchModifyAck,
    BatchModifyLegResult,
    FundingRateUpdate,
    MarginAlert,
    MassQuoteAck,
    MassQuoteLegResult,
    OrderAck,
    OrderUpdate,
    PositionsSnapshot,
    PositionUpdate,
    SettlementUpdate,
    SystemHealthUpdate,
    UnknownSequencerPush,
)

TStream = TypeVar("TStream")

logger = logging.getLogger("godark")

_REQUEST_TYPE_MAP = {"place": "place", "cancel": "cancel", "modify": "modify"}

# Encrypted command request_type -> the ack message_type it resolves on. Batched
# commands get their own ack type so an async order "ack" pushed mid-flight does
# not resolve them early; everything else falls back to the generic "ack".
_INFLIGHT_ACK_TYPE = {
    "mass_quote": "mass_quote_ack",
    "batch_cancel": "batch_cancel_ack",
    "batch_modify": "batch_modify_ack",
}

# Testnet WebSocket origin (GodarkClient appends `/ws/v1`).
_DEFAULT_EDGE_BASE_URL = "wss://api.godark-dex.com"

# Devnet WebSocket origin (GodarkClient appends `/ws/v1`).
_DEVNET_EDGE_BASE_URL = "ws://18.143.165.149:13300"

# Sequencer Noise XK static public keys (64 hex). These are public pins, not
# user secrets — Testnet and Devnet use distinct sequencer keys.
_TESTNET_NOISE_STATIC_PUBLIC_KEY_HEX = (
    "a9fdd7f26c0de36d82811e9fe1df2509960cd5b25eef037355e209b9222bea7d"
)
_DEVNET_NOISE_STATIC_PUBLIC_KEY_HEX = (
    "a6807e2f6cd04b54cc19be2fd4faea2a1239f1e2896912d91222678ab54cdd45"
)


class Environment(Enum):
    """Named deployment target.

    Selects the default edge URL and, when known, a baked-in sequencer Noise XK
    public key pin. Explicit ``base_url`` / ``noise_static_public_key_hex`` and
    the corresponding environment variables still win over these presets.
    """

    TESTNET = "testnet"
    DEVNET = "devnet"
    LOCALNET = "localnet"

    @property
    def edge_base_url(self) -> str:
        """Default edge base URL for this environment (host only)."""
        if self is Environment.DEVNET:
            return _DEVNET_EDGE_BASE_URL
        if self is Environment.LOCALNET:
            return "ws://127.0.0.1:4000"
        return _DEFAULT_EDGE_BASE_URL

    @property
    def noise_static_public_key_hex(self) -> str | None:
        """Baked-in sequencer Noise XK static public key (64 hex), when known."""
        if self is Environment.TESTNET:
            return _TESTNET_NOISE_STATIC_PUBLIC_KEY_HEX
        if self is Environment.DEVNET:
            return _DEVNET_NOISE_STATIC_PUBLIC_KEY_HEX
        return None


def _resolve_edge_base_url(explicit: str | None, default: str = _DEFAULT_EDGE_BASE_URL) -> str:
    """
    Resolve edge base URL: constructor arg wins, then env, then ``default``.

    Reads ``GODARK_EDGE_URL`` or ``GDX_EDGE_URL`` (first non-empty) so localnet /
    scripts can set the host without passing ``base_url`` in code.
    """
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip()
    for key in ("GODARK_EDGE_URL", "GDX_EDGE_URL"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return default


def _resolve_noise_static_public_key_hex(
    explicit: str | None, environment: Environment
) -> str | None:
    """Resolve HPKE/Noise pin: explicit arg → env vars → environment preset."""
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip()
    for key in (
        "GDX_HPKE_STATIC_PUBLIC_KEY",
        "GDX_HPKE_STATIC_PUBKEY",
        "GODARK_HPKE_STATIC_PUBLIC_KEY",
        "VITE_GDX_HPKE_STATIC_PUBKEY",
        "GDX_NOISE_STATIC_PUBLIC_KEY",
        "GDX_NOISE_STATIC_PUBKEY",
        "GODARK_NOISE_STATIC_PUBLIC_KEY",
    ):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return environment.noise_static_public_key_hex


def _resolve_user_uuid(explicit: str | None) -> str | None:
    """Resolve user_uuid: constructor arg wins, then env vars."""
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip()
    for key in ("GODARK_USER_UUID", "GDX_USER_UUID"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return None


def _resolve_passphrase(explicit: str | None) -> str | None:
    """Resolve passphrase: constructor arg wins, then env vars."""
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip()
    for key in ("GODARK_PASSPHRASE", "GDX_PASSPHRASE"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return None


def _rewrite_http_to_ws(url: str) -> str:
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    return url


def _ws_url(base_url: str) -> str:
    """Return the canonical WebSocket URL ending in ``/ws/v1``.

    - Rewrites ``http(s)://`` to ``ws(s)://``.
    - If ``base_url`` already ends with ``/ws/v1``, returns it unchanged.
    - If it ends with the legacy ``/ws`` suffix, upgrades it to ``/ws/v1``.
    - Otherwise, appends ``/ws/v1`` to the (slash-stripped) base.
    """
    url = _rewrite_http_to_ws(base_url.rstrip("/"))
    if url.endswith("/ws/v1"):
        return url
    if url.endswith("/ws"):
        return url + "/v1"
    return url + "/ws/v1"


def _new_correlation_id() -> bytes:
    return uuid.uuid4().bytes


def _timestamp_ns() -> int:
    return int(time.time() * 1_000_000_000)


class GodarkClient:
    """
    Async trading client for the GoDark DEX.

    Handles API-key authentication, Noise XK session negotiation, bound-AEAD
    encrypted order flow, and real-time order/position streaming.

    Parameters:
        api_key: Legacy single opaque API key.
        api_key_id: Key-pair public ID (use with ``api_secret`` and ``passphrase``).
        api_secret: Key-pair secret (use with ``api_key_id`` and ``passphrase``).
        passphrase: User-chosen API key passphrase (required with key pair; also reads
            ``GODARK_PASSPHRASE`` / ``GDX_PASSPHRASE``).
        environment: Named deployment preset (``Environment.TESTNET`` default).
            Supplies the default edge URL and, for Testnet/Devnet, each
            environment's published sequencer Noise XK pin when those are not
            set explicitly or via env.
        base_url: Edge WebSocket origin (host only, e.g.
            ``wss://api.godark-dex.com``). The client appends ``/ws/v1`` to
            produce the final upgrade URL. Preference: arg →
            ``GODARK_EDGE_URL`` / ``GDX_EDGE_URL`` → ``environment`` preset.
        user_uuid: Fallback user UUID when the edge auth response omits it
            (e.g. local edge). Also reads ``GODARK_USER_UUID`` / ``GDX_USER_UUID``.
        auto_reconnect: Automatically reconnect on disconnect.
        symbol_map: Custom symbol-name-to-id mapping.
        transport: Low-level transport config (TLS, timeouts, etc.).
        stream_buffer_size: Max buffered order/position updates.
        place_order_terminal_timeout: Seconds to wait after the fast place ack for
            an OPEN/reject/fill/cancel update when ``confirmation="book"``.
            Defaults to the command timeout.
        noise_static_public_key_hex: Pinned 32-byte sequencer X25519 static key
            in hexadecimal. Preference: arg → ``GDX_NOISE_STATIC_PUBLIC_KEY``
            (aliases ``GDX_NOISE_STATIC_PUBKEY`` /
            ``GODARK_NOISE_STATIC_PUBLIC_KEY``) → baked-in pin from
            ``environment`` (Testnet/Devnet only).

    Usage::

        async with GodarkClient(
            api_key_id="gdk_…", api_secret="…", passphrase="your-passphrase"
        ) as client:
            ...

        # Local edge (no user_uuid in auth response):
        async with GodarkClient(
            api_key="test-key-1",
            environment=Environment.LOCALNET,
            user_uuid="00000000-0000-4000-8000-000000000001",
            noise_static_public_key_hex="…",  # required on localnet
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
        passphrase: str | None = None,
        environment: Environment = Environment.TESTNET,
        base_url: str | None = None,
        user_uuid: str | None = None,
        auto_reconnect: bool = True,
        symbol_map: dict[str, int] | None = None,
        transport: TransportConfig | None = None,
        stream_buffer_size: int = 256,
        place_order_terminal_timeout: float | None = None,
        noise_static_public_key_hex: str | None = None,
    ):
        if api_key_id is not None or api_secret is not None:
            if api_key_id is None or api_secret is None:
                raise ValueError("api_key_id and api_secret must be provided together")
            if api_key is not None:
                raise ValueError("use either api_key or (api_key_id, api_secret), not both")
            resolved_passphrase = _resolve_passphrase(passphrase)
            if resolved_passphrase is None:
                raise ValueError("passphrase is required when using api_key_id and api_secret")
            self._auth_token = f"{api_key_id}:{api_secret}:{resolved_passphrase}"
        elif api_key is not None:
            if passphrase is not None and str(passphrase).strip() != "":
                raise ValueError("passphrase must not be set when using legacy api_key")
            self._auth_token = api_key
        else:
            raise ValueError("provide api_key or both api_key_id and api_secret")

        if not isinstance(environment, Environment):
            raise TypeError("environment must be an Environment")

        self._environment = environment
        self._base_url = _resolve_edge_base_url(base_url, environment.edge_base_url)
        self._config_user_uuid = _resolve_user_uuid(user_uuid)
        self._auto_reconnect = auto_reconnect
        self._user_symbol_map = symbol_map is not None
        self._symbol_map = dict(symbol_map) if symbol_map is not None else load_offline_symbol_map()
        self._transport_config = transport
        self._noise_static_public_key_hex = _resolve_noise_static_public_key_hex(
            noise_static_public_key_hex, environment
        )
        self._place_order_terminal_timeout = (
            place_order_terminal_timeout
            if place_order_terminal_timeout is not None
            else (
                transport.command_timeout
                if transport is not None and transport.command_timeout is not None
                else EdgeTransport.COMMAND_TIMEOUT
            )
        )

        if stream_buffer_size < 1:
            raise ValueError("stream_buffer_size must be >= 1")

        self._transport = EdgeTransport(_ws_url(self._base_url), self._transport_config)
        self._session = CryptoSession()
        self._conn_id = 0
        self._user_uuid: str | None = None
        self._account_id: str | None = None
        self._login_session_id: str | None = None
        self._token_expires_at: str | None = None
        self._cancel_on_disconnect = False
        self._connected = False
        # The ack message_type the currently in-flight encrypted command waits
        # for (set by _send_encrypted_command); guards batch acks from being
        # resolved early by an unrelated async "ack" push.
        self._inflight_response_type: str | None = None
        # Per-correlation expected ack type for concurrent in-flight commands.
        self._expected_ack_by_correlation: dict[str, str] = {}

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
        self._place_outcome_waiters: list[dict[str, Any]] = []
        self._recent_terminal_updates: list[OrderUpdate] = []
        self._pending_encrypted_by_nonce: dict[int, dict] = {}

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
        """Connect, authenticate, and establish an HPKE session."""
        self._intentional_close = False

        if not self._user_symbol_map:
            from .rest_client import _ws_origin_to_http_rest

            self._symbol_map = await load_symbol_map_from_edge(
                _ws_origin_to_http_rest(self._base_url)
            )

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

        try:
            self._conn_id = int(auth_result["conn_id"])
        except (KeyError, TypeError, ValueError) as exc:
            await self._transport.disconnect()
            raise AuthenticationError(
                "auth response missing non-zero conn_id (required for HPKE)"
            ) from exc
        if self._conn_id == 0:
            await self._transport.disconnect()
            raise AuthenticationError("auth response missing non-zero conn_id (required for HPKE)")

        await self._setup_hpke_session()
        self._connected = True
        self._reconnect_attempts = 0
        logger.info("GodarkClient connected and authenticated")

    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        self._intentional_close = True
        self._connected = False
        self._clear_place_outcomes(ConnectionError("Disconnected before book confirmation"))
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self._transport.disconnect()
        self._pending_encrypted_by_nonce.clear()
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
        confirmation: Literal["ack", "book"] = "book",
    ) -> OrderAck:
        """Place an order with explicit acknowledgement or book confirmation.

        ``confirmation="ack"`` returns as soon as the sequencer acknowledges the
        request. ``confirmation="book"`` (the default) waits for the subsequent
        OPEN, REJECTED, FILLED, PARTIALLY_FILLED, or CANCELLED order update.
        """
        if confirmation not in ("ack", "book"):
            raise ValueError("confirmation must be 'ack' or 'book'")
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

        waiter = self._register_place_outcome_waiter() if confirmation == "book" else None
        try:
            ack = await self._send_encrypted_order("place", symbol_id, plaintext, corr_id)
        except BaseException:
            self._cancel_place_outcome_waiter(waiter)
            raise

        if waiter is None:
            return ack
        update = await self._await_place_outcome(ack.order_id, waiter)
        if update.update_type == OrderUpdateType.REJECTED or update.status == OrderStatus.REJECTED:
            raise make_order_error_from_json(update.msg, update.reject_reason)
        return ack

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

    async def mass_quote(
        self,
        symbol: str,
        legs: list[dict],
        leverage: int = 1,
        post_only: bool | None = None,
    ) -> MassQuoteAck:
        """Bulk cancel-replace (market-maker mass quote).

        Each ``leg`` is a dict with: ``side`` ("BUY"/"SELL" or :class:`Side`),
        ``price`` (float), ``quantity`` (float), optional ``cancel_order_id``
        (int; omit/0 = pure place), ``time_in_force`` ("GTC"/"GTD", default GTC),
        ``expiry_time`` (ns, GTD only). Up to 20 legs per batch, single symbol.

        ``post_only`` controls the batch matching mode. Left as ``None`` (the
        default) every replacement is post-only: a leg that would cross is
        rejected as ``failed``, which lets the whole batch fuse into one MPC
        round. Pass ``post_only=False`` for the relaxed path, where a crossing
        leg instead takes liquidity up to its limit and rests the remainder; the
        number of taker fills is reported per leg as ``fill_count``. Both modes
        keep online MPC rounds flat in the number of legs.

        Returns a :class:`MassQuoteAck` with one result per leg.
        """
        self._ensure_ready()
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()

        plaintext = _proto.build_mass_quote_proto(
            symbol_id=symbol_id,
            user_uuid=self._user_uuid_bytes(),
            legs=legs,
            correlation_id_bytes=corr_id,
            leverage=leverage,
            post_only=post_only,
        )
        response = await self._send_encrypted_command(
            "mass_quote", "order.mass_quote", symbol_id, plaintext, corr_id
        )
        return self._parse_mass_quote_response(response)

    async def batch_cancel(
        self,
        symbol: str,
        order_ids: list[int],
    ) -> BatchCancelAck:
        """Cancel multiple resting orders in a single fanned-out request.

        ``order_ids`` is a list of resting order ids on one ``symbol`` (up to 20
        per batch). Cancels are pure index removals (no MPC comparison), so the
        whole batch costs zero online rounds regardless of count. Returns a
        :class:`BatchCancelAck` with one result per id, in input order; an id
        that is not resting is reported as ``cancelled=False`` (error_code 2003)
        and never aborts the rest of the batch.
        """
        self._ensure_ready()
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()

        plaintext = _proto.build_batch_cancel_proto(
            symbol_id=symbol_id,
            user_uuid=self._user_uuid_bytes(),
            order_ids=order_ids,
            correlation_id_bytes=corr_id,
        )
        response = await self._send_encrypted_command(
            "batch_cancel", "order.batch_cancel", symbol_id, plaintext, corr_id
        )
        return self._parse_batch_cancel_response(response)

    async def batch_modify(
        self,
        symbol: str,
        legs: list[dict],
    ) -> BatchModifyAck:
        """Amend multiple resting orders in a single fanned-out post-only request.

        ``legs`` is a list of dicts on one ``symbol`` (up to 20 per batch); each
        leg supports ``order_id`` (int, required), ``new_price`` (float|None) and
        ``new_quantity`` (float|None) — at least one of the two must be set.
        Amends are post-only: a leg whose amended order would cross is rejected
        (``modified=False``, error_code 2018) rather than taking liquidity, and a
        missing order id is reported ``modified=False`` (error_code 2003); neither
        aborts the rest of the batch. Returns a :class:`BatchModifyAck` with one
        result per leg, in input order.
        """
        self._ensure_ready()
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()

        plaintext = _proto.build_batch_modify_proto(
            symbol_id=symbol_id,
            user_uuid=self._user_uuid_bytes(),
            legs=legs,
            correlation_id_bytes=corr_id,
        )
        response = await self._send_encrypted_command(
            "batch_modify", "order.batch_modify", symbol_id, plaintext, corr_id
        )
        return self._parse_batch_modify_response(response)

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
    # Internals: Noise XK session
    # ------------------------------------------------------------------

    async def _setup_hpke_session(self) -> None:
        """Complete HPKE Base setup over the active WebSocket."""
        if self._user_uuid is None or self._conn_id == 0:
            raise SessionError("user_uuid and conn_id required before HPKE setup")
        try:
            remote_static = pinned_sequencer_static_pub(self._noise_static_public_key_hex)
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        try:
            user = uuid.UUID(self._user_uuid)
            encapped = self._session.setup(remote_static, user, self._conn_id)
        except Exception as exc:
            raise SessionError(f"HPKE setup failed: {exc}") from exc
        from ._wire import encode_hpke_setup

        try:
            frame = encode_hpke_setup(user.bytes, self._conn_id, encapped)
            reply = await self._transport.send_hpke_setup(frame)
            if reply.get("established") is not True:
                raise SessionError("HPKE setup not established")
            reply_conn_id = reply.get("conn_id")
            if reply_conn_id != self._conn_id:
                raise SessionError(
                    f"HPKE setup conn_id mismatch: expected {self._conn_id}, got {reply_conn_id}"
                )
            self._session.establish()
        except SessionError:
            self._session.abort_setup()
            raise
        except Exception as exc:
            self._session.abort_setup()
            raise SessionError(f"HPKE setup failed: {exc}") from exc
        logger.info("HPKE session established (conn_id=%s)", self._conn_id)

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
        op_map = {"place": "order.place", "cancel": "order.cancel", "modify": "order.modify"}
        response = await self._send_encrypted_command(
            request_type, op_map[request_type], symbol_id, plaintext, correlation_id
        )
        return self._parse_order_response(response)

    async def _send_encrypted_command(
        self,
        request_type: str,
        docs_op: str,
        symbol_id: int,
        plaintext: bytes,
        correlation_id: bytes = b"",
    ) -> dict:
        """Encrypt one edge command and await its raw transport response.

        Shared by order place/cancel/modify, mass quote and batch cancel/modify.
        ``request_type`` sets the encrypted ``OrderHeader`` request type (also used
        as AES-GCM AAD); ``docs_op`` is the WS docs op string in docs-wire mode.
        """
        body_length = CryptoSession.body_length_for_plaintext(len(plaintext))
        corr_id_str = correlation_id.hex() if len(correlation_id) == 16 else ""
        expected_ack = _INFLIGHT_ACK_TYPE.get(request_type, "ack")

        # Encryption assigns and advances the session send-nonce, so it must be
        # atomic with the actual send to keep concurrent commands in nonce
        # order on the wire. ``prepare`` runs under the transport send lock.
        def _prepare() -> bytes:
            nonce_counter = self._session.next_nonce
            aad = _proto.build_order_header_aad(
                user_uuid=self._user_uuid_bytes(),
                symbol_id=symbol_id,
                request_type_str=request_type,
                nonce=nonce_counter,
                body_length=body_length,
                correlation_id=correlation_id,
                conn_id=self._conn_id,
            )
            try:
                actual_nonce, ciphertext = self._session.encrypt_order(aad, plaintext)
            except Exception as e:
                raise EncryptionError(f"Failed to encrypt order: {e}") from e

            header = build_order_header_proto(
                user_uuid=self._user_uuid_bytes(),
                symbol_id=symbol_id,
                request_type_str=request_type,
                nonce=actual_nonce,
                body_length=body_length,
                correlation_id=correlation_id,
                conn_id=self._conn_id,
            )
            return encode_encrypted_order(encrypted_order_request(header, ciphertext))

        # Record the ack type this command expects, keyed by correlation id so
        # concurrent commands don't clobber one another. Falls back to the
        # single-slot field when no correlation id is present (legacy path).
        if corr_id_str:
            self._expected_ack_by_correlation[corr_id_str.lower()] = expected_ack
        else:
            self._inflight_response_type = expected_ack
        try:
            return await self._transport.send_binary_command(
                prepare=_prepare,
                correlation_id=corr_id_str,
            )
        finally:
            if corr_id_str:
                self._expected_ack_by_correlation.pop(corr_id_str.lower(), None)
            else:
                self._inflight_response_type = None

    def _parse_order_response(self, msg: dict) -> OrderAck:
        msg_type = msg.get("type")

        if msg_type == "error":
            raise make_order_error_from_json(msg.get("message"), msg.get("error_code"))

        if msg_type == "ack":
            if not msg.get("success"):
                raw_code = msg.get("error_code")
                raise make_order_error_from_json(
                    msg.get("reject_text")
                    or msg.get("msg")
                    or msg.get("error")
                    or msg.get("message"),
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

    def _parse_mass_quote_response(self, msg: dict) -> MassQuoteAck:
        msg_type = msg.get("type")
        if msg_type == "error":
            raise OrderError(msg.get("message", "unknown error"))
        if msg_type != "encrypted_push":
            raise OrderError(f"Unexpected mass quote response type: {msg_type}")

        plaintext = self._decrypt_push_body(msg, "mass_quote_ack")

        parsed = _proto.parse_mass_quote_ack(plaintext)
        if parsed.get("type") != "mass_quote_ack":
            reject = _proto.parse_node_response(plaintext)
            if reject.get("type") == "ack" and not reject.get("success", True):
                raise make_order_error_from_json(
                    reject.get("reject_text") or reject.get("message"),
                    str(reject["error_code"]) if reject.get("error_code") is not None else None,
                )
            raise OrderError(f"Expected mass_quote_ack, got {parsed.get('type')}")

        results = [
            MassQuoteLegResult(
                leg_index=r["leg_index"],
                status=r["status"],
                cancelled_order_id=(
                    str(r["cancelled_order_id"]) if r["cancelled_order_id"] else None
                ),
                new_order_id=str(r["new_order_id"]) if r["new_order_id"] else None,
                error_code=r["error_code"],
                fill_count=r.get("fill_count", 0),
            )
            for r in parsed.get("results", [])
        ]
        success = bool(results) and all(r.status != "failed" for r in results)
        return MassQuoteAck(
            success=success,
            sequence=str(parsed.get("sequence", "")),
            results=results,
        )

    def _parse_batch_cancel_response(self, msg: dict) -> BatchCancelAck:
        msg_type = msg.get("type")
        if msg_type == "error":
            raise OrderError(msg.get("message", "unknown error"))
        if msg_type != "encrypted_push":
            raise OrderError(f"Unexpected batch cancel response type: {msg_type}")

        plaintext = self._decrypt_push_body(msg, "batch_cancel_ack")

        parsed = _proto.parse_batch_cancel_ack(plaintext)
        if parsed.get("type") != "batch_cancel_ack":
            reject = _proto.parse_node_response(plaintext)
            if reject.get("type") == "ack" and not reject.get("success", True):
                raise make_order_error_from_json(
                    reject.get("reject_text") or reject.get("message"),
                    str(reject["error_code"]) if reject.get("error_code") is not None else None,
                )
            raise OrderError(f"Expected batch_cancel_ack, got {parsed.get('type')}")

        results = [
            BatchCancelLegResult(
                order_id=str(r["order_id"]),
                cancelled=r["cancelled"],
                error_code=r["error_code"],
            )
            for r in parsed.get("results", [])
        ]
        success = bool(results) and all(r.cancelled for r in results)
        return BatchCancelAck(
            success=success,
            sequence=str(parsed.get("sequence", "")),
            results=results,
        )

    def _parse_batch_modify_response(self, msg: dict) -> BatchModifyAck:
        msg_type = msg.get("type")
        if msg_type == "error":
            raise OrderError(msg.get("message", "unknown error"))
        if msg_type != "encrypted_push":
            raise OrderError(f"Unexpected batch modify response type: {msg_type}")

        plaintext = self._decrypt_push_body(msg, "batch_modify_ack")

        parsed = _proto.parse_batch_modify_ack(plaintext)
        if parsed.get("type") != "batch_modify_ack":
            reject = _proto.parse_node_response(plaintext)
            if reject.get("type") == "ack" and not reject.get("success", True):
                raise make_order_error_from_json(
                    reject.get("reject_text") or reject.get("message"),
                    str(reject["error_code"]) if reject.get("error_code") is not None else None,
                )
            raise OrderError(f"Expected batch_modify_ack, got {parsed.get('type')}")

        results = [
            BatchModifyLegResult(
                order_id=str(r["order_id"]),
                modified=r["modified"],
                error_code=r["error_code"],
            )
            for r in parsed.get("results", [])
        ]
        success = bool(results) and all(r.modified for r in results)
        return BatchModifyAck(
            success=success,
            sequence=str(parsed.get("sequence", "")),
            results=results,
        )

    def _decrypt_ack_push(self, msg: dict) -> OrderAck:
        if msg.get("_decrypt_error"):
            raise EncryptionError(f"Failed to decrypt ack: {msg['_decrypt_error']}")
        try:
            plaintext = self._decrypt_push_body(msg, "ack")
        except Exception as e:
            raise EncryptionError(f"Failed to decrypt ack: {e}") from e

        ack_dict = _proto.parse_node_response(plaintext)
        if ack_dict.get("type") != "ack":
            raise OrderError(f"Expected ack, got {ack_dict.get('type')}")

        if not ack_dict.get("success"):
            raw_code = ack_dict.get("error_code")
            ec = raw_code if raw_code is not None else ""
            raise make_order_error_from_json(
                ack_dict.get("reject_text"),
                str(ec) if ec != "" else None,
            )

        return OrderAck(
            order_id=str(ack_dict.get("order_id", "")),
            success=True,
            sequence=str(ack_dict.get("sequence", "")),
        )

    def _decrypt_push_body(self, msg: dict, default_message_type: str) -> bytes:
        """Decrypt a Noise-bound response, retaining pre-decrypted ordered acks."""
        cached = msg.get("_decrypted_plaintext")
        if isinstance(cached, bytes):
            return cached
        ct = base64.b64decode(msg.get("encrypted_body", ""))
        nonce = int(msg.get("nonce", 0))
        aad = _proto.build_response_header_aad(
            user_uuid=self._user_uuid_bytes(),
            message_type_str=msg.get("message_type", default_message_type),
            body_length=len(ct),
            nonce=nonce,
            fencing_epoch=msg.get("fencing_epoch", 0),
            correlation_id=_proto.response_correlation_id_bytes(msg.get("correlation_id")),
            session_seq=int(msg.get("session_seq") or 0),
            conn_id=int(msg.get("conn_id") or self._conn_id),
        )
        return self._session.decrypt_push(nonce, aad, ct)

    # ------------------------------------------------------------------
    # Internals: push message handlers
    # ------------------------------------------------------------------

    def _handle_encrypted_push(self, msg: dict) -> None:
        """Decrypt and dispatch an encrypted push frame."""
        self._dispatch_encrypted_push_in_order(msg)

    def _dispatch_encrypted_push_in_order(self, msg: dict) -> None:
        message_type = msg.get("message_type", "")

        if message_type in ("ack", "mass_quote_ack", "batch_cancel_ack", "batch_modify_ack"):
            # Resolve the in-flight command for any encrypted ack whose correlation
            # id matches the waiter (matches Go/Rust). Reject acks for batch ops
            # must surface to the caller instead of timing out.
            try:
                msg["_decrypted_plaintext"] = self._decrypt_push_body(msg, message_type)
            except Exception as e:
                msg["_decrypt_error"] = str(e)
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

        try:
            plaintext = self._decrypt_push_body(msg, message_type)
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
            self._observe_order_update(parsed)
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

    @staticmethod
    def _is_terminal_place_update(update: OrderUpdate) -> bool:
        return update.update_type in {
            OrderUpdateType.OPEN,
            OrderUpdateType.REJECTED,
            OrderUpdateType.FILLED,
            OrderUpdateType.PARTIALLY_FILLED,
            OrderUpdateType.CANCELLED,
        } or update.status in {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.REJECTED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
        }

    def _register_place_outcome_waiter(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        waiter: dict[str, Any] = {
            "order_id": None,
            "future": loop.create_future(),
        }
        self._place_outcome_waiters.append(waiter)
        return waiter

    def _cancel_place_outcome_waiter(self, waiter: dict[str, Any] | None) -> None:
        if waiter is None:
            return
        with contextlib.suppress(ValueError):
            self._place_outcome_waiters.remove(waiter)
        future = waiter["future"]
        if not future.done():
            future.cancel()
        elif not future.cancelled():
            with contextlib.suppress(BaseException):
                future.exception()

    async def _await_place_outcome(self, order_id: str, waiter: dict[str, Any]) -> OrderUpdate:
        waiter["order_id"] = order_id
        buffered = next(
            (u for u in self._recent_terminal_updates if u.order_id == order_id),
            None,
        )
        if buffered is not None and not waiter["future"].done():
            waiter["future"].set_result(buffered)
            self._recent_terminal_updates.remove(buffered)
        try:
            return await asyncio.wait_for(
                waiter["future"], timeout=self._place_order_terminal_timeout
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                "place_order timed out waiting for terminal update after "
                f"{self._place_order_terminal_timeout:g}s"
            ) from exc
        finally:
            with contextlib.suppress(ValueError):
                self._place_outcome_waiters.remove(waiter)

    def _clear_place_outcomes(self, error: BaseException) -> None:
        waiters, self._place_outcome_waiters = self._place_outcome_waiters, []
        self._recent_terminal_updates.clear()
        for waiter in waiters:
            future = waiter["future"]
            if not future.done():
                future.set_exception(type(error)(str(error)))

    def _observe_order_update(self, update: OrderUpdate) -> None:
        if not self._is_terminal_place_update(update):
            return
        for waiter in self._place_outcome_waiters:
            if waiter["order_id"] == update.order_id and not waiter["future"].done():
                waiter["future"].set_result(update)
                return
        self._recent_terminal_updates.append(update)
        if len(self._recent_terminal_updates) > 64:
            self._recent_terminal_updates.pop(0)

    # ------------------------------------------------------------------
    # Internals: reconnect & rekey
    # ------------------------------------------------------------------

    async def _handle_rekey(self, msg: dict) -> None:
        logger.info("Rekey required, re-negotiating HPKE session")
        try:
            self._session.reset()
            self._pending_encrypted_by_nonce.clear()
            await self._setup_hpke_session()
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
        self._pending_encrypted_by_nonce.clear()
        self._clear_place_outcomes(ConnectionError("Disconnected before book confirmation"))
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
            raise SessionError("HPKE session not established")

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
