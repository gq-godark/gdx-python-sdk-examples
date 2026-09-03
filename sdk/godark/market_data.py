"""Market data WebSocket client (public ``/ws/v1`` or legacy ``/ws/gomarket``)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from ._transport import EdgeTransport, TransportConfig
from .client import _ws_url
from .errors import GodarkError

logger = logging.getLogger("godark.market_data")

_ORDERBOOK_DOCS_WIRE_MSG = (
    "L2 orderbook is not available on /ws/v1; set GODARK_MARKET_DATA_WS_URL to a direct L2 "
    "stream URL, use subscribe_public_channel for public edge feeds, or "
    "GODARK_MARKET_DATA_USE_GOMARKET=1 for local /ws/gomarket"
)


def _is_docs_wire_url(url: str) -> bool:
    path = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return path.endswith("/ws/v1")


def _env_first(*keys: str) -> str | None:
    for key in keys:
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return None


def _env_truthy(*keys: str) -> bool:
    for key in keys:
        raw = os.environ.get(key, "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
    return False


def gomarket_ws_url(base_url: str) -> str:
    """Strip edge ``/ws`` suffixes and append ``/ws/gomarket`` (WebSocket scheme)."""
    url = base_url.rstrip("/")
    if url.endswith("/ws/v1"):
        url = url[: -len("/ws/v1")]
    elif url.endswith("/ws"):
        url = url[: -len("/ws")]
    if url.startswith("http://"):
        url = "ws://" + url[len("http://") :]
    elif url.startswith("https://"):
        url = "wss://" + url[len("https://") :]
    return url + "/ws/gomarket"


def resolve_market_data_ws_url(base_url: str) -> str:
    """
    Resolve the market-data WebSocket URL.

    Hosted edges default to the canonical trading path ``/ws/v1``. Set
    ``GODARK_MARKET_DATA_WS_URL`` for a full override, or
    ``GODARK_MARKET_DATA_USE_GOMARKET=1`` for the legacy multiplex at
    ``/ws/gomarket``.
    """
    override = _env_first("GODARK_MARKET_DATA_WS_URL", "GDX_MARKET_DATA_WS_URL")
    if override:
        return override
    if _env_truthy("GODARK_MARKET_DATA_USE_GOMARKET", "GDX_MARKET_DATA_USE_GOMARKET"):
        return gomarket_ws_url(base_url.strip())
    return _ws_url(base_url.strip())


def subscription_callback_key(msg: dict[str, Any]) -> str | None:
    """
    Map a market-data server message to the callback dict key ``{channel}:{symbol}``.

    Data events use ``type`` of ``orderbook`` or ``trade`` (singular); the
    SDK registers callbacks under ``orderbook`` and ``trades`` respectively.
    Snapshot types for public edge feeds map to ``volume:``, ``open_interest:``,
    and ``funding_rate:``. Control messages return None so user callbacks are
    not invoked.
    """
    typ = msg.get("type")
    if typ in (
        "status",
        "subscribed",
        "unsubscribed",
        "pong",
        "error",
    ):
        return None
    symbol = msg.get("symbol") or ""
    if typ == "orderbook":
        return f"orderbook:{symbol}"
    if typ == "trade":
        return f"trades:{symbol}"
    if typ == "volume_snapshot":
        return "volume:"
    if typ == "open_interest_snapshot":
        return "open_interest:"
    if typ == "funding_rate_snapshot":
        return "funding_rate:"
    # Legacy: channel + symbol (tests / older wire)
    channel = msg.get("channel") or ""
    if channel:
        return f"{channel}:{symbol}"
    return None


class MarketDataClient:
    """
    Async market data client for public edge feeds.

    Default URL is ``/ws/v1`` (docs wire). Opt into legacy GoMarket with
    ``GODARK_MARKET_DATA_USE_GOMARKET=1``.

    Usage:
        client = MarketDataClient("wss://api.godark-dex.com")
        await client.connect()
        await client.subscribe_public_channel("volume", callback)
        # ... later
        await client.disconnect()
    """

    HEARTBEAT_INTERVAL = EdgeTransport.HEARTBEAT_INTERVAL

    def __init__(self, base_url: str, transport: TransportConfig | None = None):
        self._url = resolve_market_data_ws_url(base_url.strip())
        self._docs_wire = _is_docs_wire_url(self._url)
        self._transport_config = transport or TransportConfig()
        self._heartbeat_interval: float = (
            self._transport_config.heartbeat_interval
            if self._transport_config.heartbeat_interval is not None
            else EdgeTransport.HEARTBEAT_INTERVAL
        )
        self._stale_timeout: float = (
            self._transport_config.stale_timeout
            if self._transport_config.stale_timeout is not None
            else EdgeTransport.STALE_TIMEOUT
        )
        self._missed_heartbeat_limit: int = (
            self._transport_config.missed_heartbeat_limit
            if self._transport_config.missed_heartbeat_limit is not None
            else EdgeTransport.MISSED_HEARTBEAT_LIMIT
        )
        self._ws: ClientConnection | None = None
        self._connected = False
        self._last_inbound: float = 0.0
        self._inbound_since_ping = True
        self._missed_heartbeat_count = 0
        self._recv_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._callbacks: dict[str, Callable] = {}
        self._auto_reconnect = True
        # (channel, symbol) for symbol streams; public channels use ("__public__", channel)
        self._desired_subs: set[tuple[str, str]] = set()

    def _connect_kwargs(self) -> dict:
        kw: dict = {
            "max_size": self._transport_config.max_size
            if self._transport_config.max_size is not None
            else 1_048_576,
        }
        if self._transport_config.ssl is not None:
            kw["ssl"] = self._transport_config.ssl
        if self._transport_config.additional_headers:
            kw["additional_headers"] = dict(self._transport_config.additional_headers)
        if self._transport_config.proxy is not True:
            kw["proxy"] = self._transport_config.proxy
        if self._transport_config.open_timeout is not None:
            kw["open_timeout"] = self._transport_config.open_timeout
        return kw

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def _note_inbound(self) -> None:
        self._last_inbound = time.monotonic()
        self._inbound_since_ping = True
        self._missed_heartbeat_count = 0

    async def connect(self) -> None:
        """Open WebSocket to the resolved market-data endpoint."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._recv_task:
            self._recv_task.cancel()
        self._ws = await websockets.connect(self._url, **self._connect_kwargs())
        self._connected = True
        self._last_inbound = time.monotonic()
        self._inbound_since_ping = True
        self._missed_heartbeat_count = 0
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self._resubscribe_all()
        logger.info("Market data connected to %s", self._url)

    async def disconnect(self) -> None:
        self._connected = False
        self._auto_reconnect = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    async def subscribe_orderbook(self, symbol: str, callback: Callable) -> None:
        """Subscribe to L2 orderbook updates for a symbol (gomarket only)."""
        if self._docs_wire:
            raise GodarkError(_ORDERBOOK_DOCS_WIRE_MSG)
        await self._subscribe("orderbook", symbol, callback)

    async def subscribe_trades(self, symbol: str, callback: Callable) -> None:
        """Subscribe to trade updates for a symbol."""
        await self._subscribe("trades", symbol, callback)

    async def subscribe_public_channel(self, channel: str, callback: Callable) -> None:
        """
        Public edge channel on ``/ws/v1`` (no auth): ``volume``, ``open_interest``,
        ``funding_rate``.
        """
        if not channel:
            raise GodarkError("channel is required")
        if not self._docs_wire:
            raise GodarkError("subscribe_public_channel requires /ws/v1 edge URL")
        key = f"{channel}:"
        self._callbacks[key] = callback
        self._desired_subs.add(("__public__", channel))
        if self._ws:
            await self._send_public_subscribe(channel)

    async def unsubscribe(self, channel: str, symbol: str) -> None:
        key = f"{channel}:{symbol}"
        self._callbacks.pop(key, None)
        self._callbacks.pop(f"{channel}:", None)
        self._desired_subs.discard((channel, symbol))
        self._desired_subs.discard(("__public__", channel))
        if self._ws:
            await self._send_unsubscribe(channel, symbol)

    async def _subscribe(self, channel: str, symbol: str, callback: Callable) -> None:
        key = f"{channel}:{symbol}"
        self._callbacks[key] = callback
        self._desired_subs.add((channel, symbol))
        if self._ws:
            await self._send_subscribe(channel, symbol)

    async def _send_public_subscribe(self, channel: str) -> None:
        assert self._ws is not None
        await self._ws.send(
            json.dumps(
                {
                    "id": str(uuid.uuid4()),
                    "op": "subscribe",
                    "args": [{"channel": channel}],
                }
            )
        )

    async def _send_subscribe(self, channel: str, symbol: str) -> None:
        assert self._ws is not None
        if self._docs_wire:
            payload: dict[str, Any] = {
                "id": str(uuid.uuid4()),
                "op": "subscribe",
                "args": [{"channel": channel, "symbol": symbol}],
            }
        else:
            payload = {
                "action": "subscribe",
                "channel": channel,
                "symbol": symbol,
            }
        await self._ws.send(json.dumps(payload))

    async def _send_unsubscribe(self, channel: str, symbol: str) -> None:
        assert self._ws is not None
        if self._docs_wire:
            arg: dict[str, Any] = {"channel": channel}
            if symbol:
                arg["symbol"] = symbol
            payload: dict[str, Any] = {
                "id": str(uuid.uuid4()),
                "op": "unsubscribe",
                "args": [arg],
            }
        else:
            payload = {
                "action": "unsubscribe",
                "channel": channel,
                "symbol": symbol,
            }
        await self._ws.send(json.dumps(payload))

    async def _resubscribe_all(self) -> None:
        if not self._ws:
            return
        for channel, symbol in self._desired_subs:
            if channel == "__public__":
                await self._send_public_subscribe(symbol)
            else:
                await self._send_subscribe(channel, symbol)

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                self._note_inbound()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                key = subscription_callback_key(msg)
                if key is None:
                    if msg.get("type") == "error":
                        logger.warning("Market data server error: %s", msg)
                    continue
                cb = self._callbacks.get(key)
                if cb:
                    try:
                        result = cb(msg)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("Market data callback error: %s", e)
        except websockets.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("Market data recv error: %s", e)
        finally:
            self._connected = False
            if self._auto_reconnect and (
                self._reconnect_task is None or self._reconnect_task.done()
            ):
                self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _heartbeat_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(self._heartbeat_interval)
                if not self._connected:
                    break
                elapsed = time.monotonic() - self._last_inbound
                if elapsed > self._stale_timeout:
                    reason = f"stale heartbeat: no inbound message for {self._stale_timeout:.0f}s"
                    logger.warning(
                        "Market data stale connection (%.1fs no inbound), closing", elapsed
                    )
                    if self._ws:
                        await self._ws.close(4000, reason)
                    break

                if not self._inbound_since_ping:
                    self._missed_heartbeat_count += 1
                else:
                    self._missed_heartbeat_count = 0

                if self._missed_heartbeat_count >= self._missed_heartbeat_limit:
                    reason = (
                        f"stale heartbeat: missed {self._missed_heartbeat_count} "
                        f"heartbeat responses (limit {self._missed_heartbeat_limit})"
                    )
                    logger.warning("Market data %s, closing", reason)
                    if self._ws:
                        await self._ws.close(4000, reason)
                    break

                if self._ws and self._connected:
                    try:
                        if self._docs_wire:
                            ping: dict[str, Any] = {
                                "id": str(uuid.uuid4()),
                                "op": "ping",
                                "args": {},
                            }
                        else:
                            ping = {"action": "ping"}
                        await self._ws.send(json.dumps(ping))
                        self._inbound_since_ping = False
                    except Exception as exc:
                        reason = f"stale heartbeat: ping send failed: {exc}"
                        logger.warning("Market data %s", reason)
                        if self._ws:
                            with contextlib.suppress(Exception):
                                await self._ws.close(4000, reason)
                        break
        except asyncio.CancelledError:
            return

    async def _reconnect(self) -> None:
        delay = 1.0
        max_delay = 15.0
        while self._auto_reconnect:
            await asyncio.sleep(delay)
            try:
                await self.connect()
                logger.info("Market data reconnected")
                return
            except Exception as e:
                logger.warning("Market data reconnect failed: %s", e)
                delay = min(delay * 2, max_delay)
