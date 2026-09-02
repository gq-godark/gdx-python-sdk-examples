#!/usr/bin/env python3
"""REST-only trader demo — auth + encrypted snapshots + place/modify/cancel."""

from __future__ import annotations

import asyncio
import sys

from dotenv import get_first, load_dotenv
from godark import GodarkRestClient


def _live_price() -> float:
    raw = get_first("GDX_LIVE_PRICE", "GODARK_LIVE_PRICE")
    if raw:
        return float(raw)
    return 78000.0


def _rest_limit_price() -> float:
    """BUY limit well below touch so place/modify/cancel stay in the book."""
    return _live_price() - 5000.0


async def main() -> int:
    load_dotenv()

    rest = get_first("GODARK_REST_URL", "GDX_REST_URL", default="https://api.godark-dex.com")
    kid = get_first("GODARK_API_KEY_ID", "GDX_API_KEY_ID")
    secret = get_first("GODARK_API_SECRET", "GDX_API_SECRET")
    pp = get_first("GODARK_PASSPHRASE", "GDX_PASSPHRASE")
    api_key = get_first("GODARK_API_KEY", "GDX_API_KEY")
    if kid and secret:
        if not pp:
            print(
                "Set GODARK_PASSPHRASE (or GDX_PASSPHRASE) when using API key id + secret.",
                file=sys.stderr,
            )
            return 1
        client = GodarkRestClient(
            api_key_id=kid, api_secret=secret, passphrase=pp, rest_base_url=rest
        )
    elif api_key:
        client = GodarkRestClient(api_key=api_key, rest_base_url=rest)
    else:
        print(
            "Missing credentials: set GODARK_API_KEY_ID, GODARK_API_SECRET and "
            "GODARK_PASSPHRASE (or GODARK_API_KEY for localnet).",
            file=sys.stderr,
        )
        return 1

    price = _rest_limit_price()
    async with client:
        print(
            f"identity: user_uuid={client.user_uuid_str} scope={client.token_scope}"
        )
        print("open_orders", len((await client.get_open_orders()).rows))
        print("positions", len((await client.get_positions()).rows))
        acct = await client.get_account()
        if acct.account:
            print("account total_collateral=", acct.account.total_collateral)

        ack = await client.place_order(
            "BTC-USDC-PERP",
            "BUY",
            type="LIMIT",
            quantity=0.01,
            price=price,
            client_order_id="sdk-python-rest-demo",
        )
        print("placed", ack)

        await asyncio.sleep(0.5)
        modify_ack = await client.modify_order(
            ack.order_id,
            "BTC-USDC-PERP",
            new_price=price - 64,
        )
        print("modified", modify_ack)

        cancel_ack = await client.cancel_order(ack.order_id, "BTC-USDC-PERP")
        print("cancelled", cancel_ack)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
