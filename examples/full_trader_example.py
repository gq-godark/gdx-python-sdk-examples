#!/usr/bin/env python3
"""Expanded WS trading demo with callbacks and place/modify/cancel."""

from __future__ import annotations

import asyncio

from godark import GodarkClient, GodarkError, MarketDataClient, OrderType, Side, TimeInForce

from common import require_credentials, ws_base


async def main() -> None:
    key_id, secret = require_credentials()
    base_url = ws_base()

    client = GodarkClient(
        api_key_id=key_id,
        api_secret=secret,
        base_url=base_url,
        auto_reconnect=True,
        stream_buffer_size=512,
    )
    md = MarketDataClient(base_url=base_url)

    client.on_order_update(lambda u: print(f"[order] {u.update_type} {u.order_id} {u.status}"))
    client.on_position_update(lambda p: print(f"[pos] side={p.side} size={p.size}"))
    client.on_reconnect(lambda: print("[sdk] reconnected and re-subscribed"))
    client.on_error(lambda e: print(f"[sdk] non-fatal error: {e}"))

    await client.connect()
    await client.subscribe(["orders", "positions"])
    await md.connect()

    symbol = "BTC-USDT-PERP"
    await md.subscribe_orderbook(symbol, lambda m: None)

    try:
        buy = await client.place_order(
            symbol=symbol,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.001,
            price=10000.0,
            time_in_force=TimeInForce.GTC,
        )
        print(f"Placed BUY: {buy.order_id}")

        try:
            mod = await client.modify_order(
                order_id=buy.order_id,
                symbol=symbol,
                new_price=10001.0,
            )
            print(f"Modified BUY: {mod.order_id}")
        except GodarkError as e:
            print(f"Modify skipped/rejected: {e}")

        sell = await client.place_order(
            symbol=symbol,
            side=Side.SELL,
            order_type=OrderType.LIMIT,
            quantity=0.001,
            price=999999.0,
            time_in_force=TimeInForce.GTC,
        )
        print(f"Placed SELL: {sell.order_id}")
        cancel = await client.cancel_order(order_id=sell.order_id, symbol=symbol)
        print(f"Cancelled SELL: {cancel.order_id}")

        # Drain a few queued updates.
        seen = 0
        async for u in client.order_updates():
            print(f"[queued] {u.order_id} {u.status}")
            seen += 1
            if seen >= 5:
                break
    finally:
        await md.disconnect()
        await client.disconnect()
        print("Disconnected")


if __name__ == "__main__":
    asyncio.run(main())
