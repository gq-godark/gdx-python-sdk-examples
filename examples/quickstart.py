#!/usr/bin/env python3
"""Minimal darkpool MM example — post-only limit sell from the live mark, then cancel that order."""

from __future__ import annotations

import asyncio
import sys

from dotenv import get_first, load_dotenv, print_order_error
from godark import (
    Environment,
    GodarkClient,
    GodarkRestClient,
    OrderType,
    PlaceOrderOptions,
    Side,
    TimeInForce,
)
from trade_safety import (
    QTY,
    SYMBOL,
    assert_own_orders_flat,
    cancel_own,
    flatten_positions,
    position_fingerprint,
    post_only_price,
    read_book,
    resolve_live_mark,
)


async def main() -> int:
    load_dotenv()

    legacy_key = get_first("GODARK_API_KEY", "GDX_API_KEY")
    client_kwargs: dict = {"environment": Environment.TESTNET}
    if edge := get_first("GODARK_EDGE_URL", "GDX_EDGE_URL"):
        client_kwargs["base_url"] = edge
    account = get_first("GODARK_ACCOUNT", "GDX_ACCOUNT")
    deprecated_user_uuid = get_first("GODARK_USER_UUID", "GDX_USER_UUID")
    if account:
        client_kwargs["account"] = account
    elif deprecated_user_uuid:
        # Compatibility only. New integrations should configure GODARK_ACCOUNT.
        client_kwargs["user_uuid"] = deprecated_user_uuid
    if legacy_key:
        client_kwargs["api_key"] = legacy_key
    else:
        api_key_id = get_first("GODARK_API_KEY_ID", "GDX_API_KEY_ID")
        api_secret = get_first("GODARK_API_SECRET", "GDX_API_SECRET")
        passphrase = get_first("GODARK_PASSPHRASE", "GDX_PASSPHRASE")
        if not (api_key_id and api_secret and passphrase):
            print(
                "Missing credentials: set GODARK_API_KEY_ID/GODARK_API_SECRET/GODARK_PASSPHRASE "
                "or legacy GODARK_API_KEY for localnet.",
                file=sys.stderr,
            )
            return 1
        client_kwargs.update(
            api_key_id=api_key_id,
            api_secret=api_secret,
            passphrase=passphrase,
        )

    rest_kwargs = {
        k: client_kwargs[k]
        for k in ("api_key", "api_key_id", "api_secret", "passphrase", "account", "user_uuid")
        if k in client_kwargs
    }
    if edge:
        rest_kwargs["rest_base_url"] = edge
    rest = GodarkRestClient(**rest_kwargs)
    own_id: str | None = None
    baseline: dict = {}
    mark = ""
    try:
        _, positions = await read_book(rest)
        baseline = position_fingerprint(positions)
        try:
            mark = await resolve_live_mark(rest, position_rows=positions.rows)
        except Exception as e:
            print(f"No live mark; placing nothing: {e}", file=sys.stderr)
            return 1
        sell_px = post_only_price(mark, Side.SELL)
        async with GodarkClient(**client_kwargs) as client:
            print(f"Connected as account={client.account or ''}")
            try:
                # Book confirmation waits on private order updates; subscribe first.
                await client.subscribe(["orders"])
                await asyncio.sleep(0.35)
                ack = await client.place_order(
                    SYMBOL,
                    Side.SELL,
                    OrderType.LIMIT,
                    QTY,
                    price=sell_px,
                    time_in_force=TimeInForce.GTC,
                    options=PlaceOrderOptions(post_only=True),
                )
                if not ack.success or not ack.order_id:
                    print(f"Place failed: {ack.error or ack.error_code}", file=sys.stderr)
                    return 1
                own_id = str(ack.order_id)
                print(f"Place OK — order_id={own_id} (post-only SELL @ {sell_px}, mark={mark})")
                await cancel_own(client, own_id)
                print(f"cancel OK — order_id={own_id}")
                await assert_own_orders_flat(rest, {own_id}, baseline)
                own_id = None
            except Exception as e:
                print_order_error("Order rejected", e)
                if own_id is not None:
                    try:
                        await cancel_own(client, own_id)
                    except Exception as cancel_err:
                        print_order_error("Cancel rejected", cancel_err)
                try:
                    await flatten_positions(client, rest, baseline, mark)
                except Exception as flat_err:
                    print(f"Flatten failed: {flat_err}", file=sys.stderr)
                return 1
    except Exception as e:
        print(f"{e}", file=sys.stderr)
        return 1
    finally:
        await rest.disconnect()

    print("Disconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
