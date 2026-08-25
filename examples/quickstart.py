#!/usr/bin/env python3
"""Minimal darkpool MM example — place far-from-market LIMIT SELL then cancel."""

from __future__ import annotations

import asyncio
import sys

from dotenv import get_first, load_dotenv, print_order_error
from godark import Environment, GodarkClient, OrderType, Side, TimeInForce

SYMBOL = "BTC-USDC-PERP"


def live_mark_price() -> float:
    raw = get_first("GODARK_E2E_PRICE", "GDX_E2E_PRICE", "GDX_LIVE_PRICE")
    if raw:
        return float(raw)
    return 79_000.0


async def main() -> int:
    load_dotenv()

    api_key_id = get_first("GODARK_API_KEY_ID", "GDX_API_KEY_ID")
    api_secret = get_first("GODARK_API_SECRET", "GDX_API_SECRET")
    passphrase = get_first("GODARK_PASSPHRASE", "GDX_PASSPHRASE")
    if not api_key_id or not api_secret or not passphrase:
        print(
            "Missing credentials: set GODARK_API_KEY_ID, GODARK_API_SECRET and "
            "GODARK_PASSPHRASE (legacy GDX_* aliases are accepted).",
            file=sys.stderr,
        )
        return 1

    client_kwargs: dict = {
        "api_key_id": api_key_id,
        "api_secret": api_secret,
        "passphrase": passphrase,
        "environment": Environment.TESTNET,
    }
    if edge := get_first("GODARK_EDGE_URL", "GDX_EDGE_URL"):
        client_kwargs["base_url"] = edge

    try:
        async with GodarkClient(**client_kwargs) as client:
            user = client.user_uuid or ""
            print(f"Connected as user_uuid={user}")
            try:
                # Book confirmation waits on private order updates; subscribe first.
                await client.subscribe(["orders"])
                await asyncio.sleep(0.35)
                mark = live_mark_price()
                sell_px = round(mark * 1.03, 1)
                ack = await client.place_order(
                    SYMBOL,
                    Side.SELL,
                    OrderType.LIMIT,
                    0.01,
                    price=sell_px,
                    time_in_force=TimeInForce.GTC,
                )
                print(f"Place OK — order_id={ack.order_id} (limit SELL @ {sell_px}, mark={mark})")
                # Allow the resting order to settle before cancel (avoids CANCEL_TOO_SOON).
                await asyncio.sleep(0.5)
                cancel_ack = await client.cancel_order(str(ack.order_id), SYMBOL)
                print(f"Cancel OK — order_id={cancel_ack.order_id}")
            except Exception as e:
                print_order_error("Order rejected", e)
                return 1
    except Exception as e:
        print(f"{e}", file=sys.stderr)
        return 1

    print("Disconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
