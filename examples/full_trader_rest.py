#!/usr/bin/env python3
"""REST trader demo — auth, snapshots, then a post-only limit priced from the live mark.

The order is cancelled by the id this process just received. A missing mark
places nothing and exits non-zero.
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import get_first, load_dotenv, print_order_error
from godark import Environment, GodarkClient, GodarkRestClient, Side
from trade_safety import (
    QTY,
    SYMBOL,
    assert_own_orders_flat,
    flatten_positions,
    position_fingerprint,
    post_only_price,
    read_book,
    resolve_live_mark,
)


async def main() -> int:
    load_dotenv()

    rest = get_first(
        "GODARK_REST_URL",
        "GDX_REST_URL",
        "GODARK_EDGE_URL",
        "GDX_EDGE_URL",
        default="https://api.godark-dex.com",
    )
    kid = get_first("GODARK_API_KEY_ID", "GDX_API_KEY_ID")
    secret = get_first("GODARK_API_SECRET", "GDX_API_SECRET")
    pp = get_first("GODARK_PASSPHRASE", "GDX_PASSPHRASE")
    api_key = get_first("GODARK_API_KEY", "GDX_API_KEY")
    identity_kwargs: dict = {}
    if account := get_first("GODARK_ACCOUNT", "GDX_ACCOUNT"):
        identity_kwargs["account"] = account
    elif deprecated_user_uuid := get_first("GODARK_USER_UUID", "GDX_USER_UUID"):
        # Compatibility only. New integrations should configure GODARK_ACCOUNT.
        identity_kwargs["user_uuid"] = deprecated_user_uuid
    if kid and secret:
        if not pp:
            print(
                "Set GODARK_PASSPHRASE (or GDX_PASSPHRASE) when using API key id + secret.",
                file=sys.stderr,
            )
            return 1
        client = GodarkRestClient(
            api_key_id=kid,
            api_secret=secret,
            passphrase=pp,
            rest_base_url=rest,
            **identity_kwargs,
        )
    elif api_key:
        client = GodarkRestClient(api_key=api_key, rest_base_url=rest, **identity_kwargs)
    else:
        print(
            "Missing credentials: set GODARK_API_KEY_ID, GODARK_API_SECRET and "
            "GODARK_PASSPHRASE (or GODARK_API_KEY for localnet).",
            file=sys.stderr,
        )
        return 1

    own_id: str | None = None
    baseline: dict = {}
    mark = ""
    code = 1
    try:
        async with client:
            print(f"identity: account={client.account_str} scope={client.token_scope}")
            _, positions = await read_book(client)
            baseline = position_fingerprint(positions)
            print("open_orders", len((await client.get_open_orders()).rows))
            print("positions", len(positions.rows))
            acct = await client.get_account()
            if acct.summary:
                print("account total_collateral=", acct.summary.total_collateral)
            try:
                mark = await resolve_live_mark(client, position_rows=positions.rows)
                buy_px = post_only_price(mark, Side.BUY, steps=1)
                modify_px = post_only_price(mark, Side.BUY, steps=2)
                print(f"Placing post-only BUY @ {buy_px} (mark={mark})...")
                mq = await client.mass_quote(
                    SYMBOL,
                    [{"side": Side.BUY, "price": buy_px, "quantity": QTY}],
                    post_only=True,
                )
                if not mq.success or len(mq.results) != 1:
                    raise RuntimeError("place failed")
                leg = mq.results[0]
                print(
                    f"placed status={leg.status} order_id={leg.new_order_id} "
                    f"fills={leg.fill_count} err={leg.error_code}"
                )
                if leg.new_order_id:
                    own_id = str(leg.new_order_id)
                if leg.status != "open" or not own_id or leg.fill_count:
                    raise RuntimeError("place did not rest post-only")

                await asyncio.sleep(1)
                modify_ack = await client.modify_order(own_id, SYMBOL, new_price=modify_px)
                if not modify_ack.success:
                    raise RuntimeError(f"modify failed: {modify_ack.error or modify_ack.error_code}")
                print(f"modified order_id={modify_ack.order_id}")

                await asyncio.sleep(1)
                cancel_ack = await client.cancel_order(own_id, SYMBOL)
                if not cancel_ack.success:
                    raise RuntimeError(f"cancel failed: {cancel_ack.error or cancel_ack.error_code}")
                print(f"cancelled order_id={cancel_ack.order_id}")
                cancelled = own_id
                own_id = None
                await assert_own_orders_flat(client, {cancelled}, baseline)
                code = 0
            except Exception as e:
                print_order_error("REST trading failed", e)
                code = 1
            if own_id is not None:
                try:
                    await asyncio.sleep(1)
                    cancel_ack = await client.cancel_order(own_id, SYMBOL)
                    if not cancel_ack.success:
                        raise RuntimeError(cancel_ack.error or cancel_ack.error_code)
                except Exception as cancel_err:
                    print_order_error("Cancel rejected", cancel_err)
                    code = 1
            if mark and baseline and code != 0:
                try:
                    await _flatten_if_needed(client, baseline, mark)
                except Exception as flat_err:
                    print(f"Flatten failed: {flat_err}", file=sys.stderr)
                    code = 1
    except Exception as e:
        print_order_error("REST trading failed", e)
        code = 1
    return code


async def _flatten_if_needed(rest: GodarkRestClient, baseline: dict, mark: str) -> None:
    """Open a WebSocket session only to send a reduce-only flatten if size appeared."""
    try:
        if not rest.bearer_token:
            await rest.connect()
        _, positions = await read_book(rest)
    except Exception:
        return
    if position_fingerprint(positions) == baseline:
        return
    edge = get_first("GODARK_EDGE_URL", "GDX_EDGE_URL", "GODARK_REST_URL", "GDX_REST_URL")
    ws_kwargs: dict = {"environment": Environment.TESTNET}
    if edge:
        ws_kwargs["base_url"] = edge
    if rest.account:
        ws_kwargs["account"] = rest.account
    kid = get_first("GODARK_API_KEY_ID", "GDX_API_KEY_ID")
    secret = get_first("GODARK_API_SECRET", "GDX_API_SECRET")
    pp = get_first("GODARK_PASSPHRASE", "GDX_PASSPHRASE")
    if kid and secret and pp:
        ws_kwargs.update(api_key_id=kid, api_secret=secret, passphrase=pp)
    else:
        api_key = get_first("GODARK_API_KEY", "GDX_API_KEY")
        if not api_key:
            return
        ws_kwargs["api_key"] = api_key
    async with GodarkClient(**ws_kwargs) as ws:
        await ws.subscribe(["orders"])
        await flatten_positions(ws, rest, baseline, mark)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
