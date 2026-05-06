#!/usr/bin/env python3
"""Public market data example (no API key required)."""

from __future__ import annotations

import argparse
import asyncio
import time

from godark import MarketDataClient

from common import ws_base


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GoDark market data example")
    p.add_argument("--symbol", default="BTC-USDT-PERP")
    p.add_argument(
        "--duration-seconds",
        type=int,
        default=30,
        help="How long to listen before exiting (default: 30)",
    )
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    base = ws_base()
    client = MarketDataClient(base_url=base)
    await client.connect()
    print(f"Connected to {base} (gomarket path)")

    counters = {"orderbook": 0, "trade": 0}

    def on_orderbook(msg: dict) -> None:
        counters["orderbook"] += 1
        bids = msg.get("bids", [])[:1]
        asks = msg.get("asks", [])[:1]
        print(f"book {args.symbol} bid={bids[0] if bids else None} ask={asks[0] if asks else None}")

    def on_trade(msg: dict) -> None:
        counters["trade"] += 1
        print(f"trade {args.symbol} price={msg.get('price')} size={msg.get('size')} side={msg.get('side')}")

    await client.subscribe_orderbook(args.symbol, on_orderbook)
    await client.subscribe_trades(args.symbol, on_trade)
    print(f"Subscribed to orderbook+trades for {args.symbol}")
    print(f"Streaming market data for {args.duration_seconds}s; Ctrl+C to stop")
    try:
        deadline = time.monotonic() + max(1, args.duration_seconds)
        while time.monotonic() < deadline:
            await asyncio.sleep(5)
            total = counters["orderbook"] + counters["trade"]
            if total == 0:
                print("waiting for market events...")
        total = counters["orderbook"] + counters["trade"]
        print(
            f"Done. orderbook_events={counters['orderbook']} trade_events={counters['trade']}"
        )
        if total == 0:
            print("No events received during listen window.")
    except KeyboardInterrupt:
        pass
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
