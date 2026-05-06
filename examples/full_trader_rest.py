#!/usr/bin/env python3
"""REST-only encrypted trading example using GodarkRestClient."""

from __future__ import annotations

import asyncio

from godark import GodarkRestClient

from common import require_credentials, rest_base


async def main() -> None:
    key_id, secret = require_credentials()
    client = GodarkRestClient(
        api_key_id=key_id,
        api_secret=secret,
        rest_base_url=rest_base(),
    )

    symbol = "BTC-USDT-PERP"
    async with client:
        ack = await client.place_order(
            symbol=symbol,
            side="BUY",
            type="LIMIT",
            quantity=0.001,
            price=10000.0,
            client_order_id="py-rest-example-001",
        )
        print(f"Placed: order_id={ack.order_id}")

        row = await client.get_order(ack.order_id)
        print(f"Snapshot: {row}")

        cancel = await client.cancel_order(ack.order_id, symbol=symbol)
        print(f"Cancelled: order_id={cancel.order_id}")


if __name__ == "__main__":
    asyncio.run(main())
