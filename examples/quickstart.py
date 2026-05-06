#!/usr/bin/env python3
"""GoDark SDK quickstart (Python): connect -> place limit sell -> cancel."""

from __future__ import annotations

import asyncio

from godark import GodarkClient, OrderType, Side

from common import require_credentials, ws_base


async def main() -> None:
    key_id, secret = require_credentials()

    client = GodarkClient(
        api_key_id=key_id,
        api_secret=secret,
        base_url=ws_base(),
        auto_reconnect=False,
    )

    await client.connect()
    print(f"Connected as user_uuid={client.user_uuid}")
    await client.subscribe(["orders", "positions"])

    symbol = "BTC-USDT-PERP"
    ack = await client.place_order(
        symbol=symbol,
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        quantity=0.001,
        price=999999.0,
    )
    print(f"Place OK: order_id={ack.order_id}")

    cancel = await client.cancel_order(order_id=ack.order_id, symbol=symbol)
    print(f"Cancel OK: order_id={cancel.order_id}")

    await client.disconnect()
    print("Disconnected")


if __name__ == "__main__":
    asyncio.run(main())
