"""Market data WebSocket client for GoMarket feed (/ws/gomarket)."""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from ._transport import TransportConfig

logger = logging.getLogger("godark.market_data")


def subscription_callback_key(msg: dict[str, Any]) -> str | None:
    """
    Map a gdx-edge gomarket_proxy server message to the callback dict key
    ``{channel}:{symbol}`` used by MarketDataClient.

    Data events use ``type`` of ``orderbook`` or ``trade`` (singular); the
    SDK registers callbacks under ``orderbook`` and ``trades`` respectively.
    Control messages (``status``, ``subscribed``, ``error``, …) return None so
    user callbacks are not invoked.
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
    # Legacy: channel + symbol (tests / older wire)
    channel = msg.get("channel") or ""
    if channel:
        return f"{channel}:{symbol}"
    return None


class MarketDataClient:
    """
    Async market data client for gdx-edge GoMarket WebSocket proxy.

    Subscribes to orderbook and trades streams (no auth required).

    Usage:
        client = MarketDataClient("wss://api.godark-dex.com")
        await client.connect()
        await client.subscribe_orderbook("BTC-USDC-PERP", callback)
        # ... later
        await client.disconnect()
    """

    HEARTBEAT_INTERVAL = 30.0

    def __init__(self, base_url: str, transport: TransportConfig | None = None):
        url = base_url.rstrip("/")
        # Strip a trailing edge-WS suffix (legacy ``/ws`` or canonical
        # ``/ws/v1``) so we always anchor the gomarket path to the host.
        if url.endswith("/ws/v1"):
            url = url[: -len("/ws/v1")]
        elif url.endswith("/ws"):
            url = url[: -len("/ws")]
        self._url = url + "/ws/gomarket"
        self._transport_config = transport or TransportConfig()
        self._ws: ClientConnection | None = None
        self._connected = False
        self._recv_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._callbacks: dict[str, Callable] = {}
        self._auto_reconnect = True
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

    async def connect(self) -> None:
        """Open WebSocket to GoMarket proxy."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._recv_task:
            self._recv_task.cancel()
        self._ws = await websockets.connect(self._url, **self._connect_kwargs())
        self._connected = True
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
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
        """Subscribe to L2 orderbook updates for a symbol."""
        await self._subscribe("orderbook", symbol, callback)

    async def subscribe_trades(self, symbol: str, callback: Callable) -> None:
        """Subscribe to trade updates for a symbol."""
        await self._subscribe("trades", symbol, callback)

    async def unsubscribe(self, channel: str, symbol: str) -> None:
        key = f"{channel}:{symbol}"
        self._callbacks.pop(key, None)
        self._desired_subs.discard((channel, symbol))
        if self._ws:
            await self._ws.send(
                json.dumps(
                    {
                        "action": "unsubscribe",
                        "channel": channel,
                        "symbol": symbol,
                    }
                )
            )

    async def _subscribe(self, channel: str, symbol: str, callback: Callable) -> None:
        key = f"{channel}:{symbol}"
        self._callbacks[key] = callback
        self._desired_subs.add((channel, symbol))
        if self._ws:
            await self._ws.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "channel": channel,
                        "symbol": symbol,
                    }
                )
            )

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
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
            if self._auto_reconnect:
                asyncio.create_task(self._reconnect())

    async def _heartbeat_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                if self._ws and self._connected:
                    try:
                        await self._ws.send(json.dumps({"action": "ping"}))
                    except Exception:
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
                for channel, symbol in self._desired_subs:
                    cb = self._callbacks.get(f"{channel}:{symbol}")
                    if cb and self._ws:
                        await self._ws.send(
                            json.dumps(
                                {
                                    "action": "subscribe",
                                    "channel": channel,
                                    "symbol": symbol,
                                }
                            )
                        )
                logger.info("Market data reconnected")
                return
            except Exception as e:
                logger.warning("Market data reconnect failed: %s", e)
                delay = min(delay * 2, max_delay)
