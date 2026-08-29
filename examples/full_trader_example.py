#!/usr/bin/env python3
"""GoDark Python SDK — trader reference example (parity with other MM distributions).

Registers callbacks for orders, positions, and all sequencer push streams, then
exercises LIMIT place / modify / cancel with a printed session summary.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict, deque

from dotenv import get_first, load_dotenv, print_order_error
from godark import (
    BalanceUpdate,
    Environment,
    FundingRateUpdate,
    GodarkClient,
    GodarkRestClient,
    MarginAlert,
    OrderType,
    OrderUpdate,
    PositionUpdate,
    PositionsSnapshot,
    SettlementUpdate,
    Side,
    SystemHealthUpdate,
    TimeInForce,
    TransportConfig,
)

SYMBOL = "BTC-USDC-PERP"


def live_mark_price() -> float:
    raw = get_first("GODARK_E2E_PRICE", "GDX_E2E_PRICE", "GDX_LIVE_PRICE")
    if raw:
        return float(raw)
    return 79_000.0


async def main() -> int:
    load_dotenv()
    sep = "=" * 60
    print(sep)
    print("  GoDark Python SDK — Trader Reference Example")
    print(sep)
    print("Order-type support in this distribution: MARKET, LIMIT")

    legacy_key = get_first("GODARK_API_KEY", "GDX_API_KEY")
    edge = get_first("GODARK_EDGE_URL", "GDX_EDGE_URL")
    print(f"Endpoint: {edge or Environment.TESTNET.edge_base_url}")

    transport = TransportConfig(
        additional_headers={"X-Trader-Tag": "python-full-trader-demo"},
        open_timeout=10.0,
        command_timeout=10.0,
        heartbeat_interval=30.0,
        stale_timeout=60.0,
    )

    counts: dict[str, int] = defaultdict(int)
    order_events: deque[OrderUpdate] = deque(maxlen=50)
    non_fatal: deque[str] = deque(maxlen=32)

    def bump(key: str) -> None:
        counts[key] += 1

    client_kwargs: dict = {
        "environment": Environment.TESTNET,
        "transport": transport,
    }
    if edge:
        client_kwargs["base_url"] = edge
    if legacy_key:
        client_kwargs["api_key"] = legacy_key
        if uid := get_first("GODARK_USER_UUID", "GDX_USER_UUID"):
            client_kwargs["user_uuid"] = uid
    else:
        api_key_id = get_first("GODARK_API_KEY_ID", "GDX_API_KEY_ID")
        api_secret = get_first("GODARK_API_SECRET", "GDX_API_SECRET")
        passphrase = get_first("GODARK_PASSPHRASE", "GDX_PASSPHRASE")
        if not (api_key_id and api_secret and passphrase):
            print(
                "Missing GODARK_API_KEY_ID / GODARK_API_SECRET / GODARK_PASSPHRASE "
                "or legacy GODARK_API_KEY for localnet.",
                file=sys.stderr,
            )
            return 1
        client_kwargs.update(
            api_key_id=api_key_id,
            api_secret=api_secret,
            passphrase=passphrase,
        )

    client = GodarkClient(**client_kwargs)

    def on_order(u: OrderUpdate) -> None:
        bump("order_update")
        order_events.append(u)

    def on_pos(u: PositionUpdate) -> None:
        bump("position_update")
        print(
            f"POS    side={u.side}  size={u.size}  entry={u.entry_price}",
            flush=True,
        )

    # BTC-USDC-PERP is symbol_id 1; capture its live mark from snapshots so the
    # mass-quote ladder/cross prices below can anchor to the real touch instead
    # of a fixed constant.
    last_mark: dict[str, float] = {}

    def on_snap(s: PositionsSnapshot) -> None:
        bump("positions_snapshot")
        print(
            f"SNAP   source={s.source}  rows={len(s.rows)}  ts={s.server_timestamp}",
            flush=True,
        )
        for row in s.rows:
            if row.symbol_id == 1 and row.mark_price:
                try:
                    last_mark["BTC"] = float(row.mark_price)
                except (TypeError, ValueError):
                    pass
            mark = row.mark_price or "—"
            print(
                f"  ↳ symbol={row.symbol_id}  side={row.side}  "
                f"size={row.size}  entry={row.entry_price}  mark={mark}",
                flush=True,
            )

    def on_health(h: SystemHealthUpdate) -> None:
        bump("system_health")
        print(
            f"HEALTH nodes={h.total_nodes}  accepting={h.accepting_orders}  "
            f"ready={h.ready}",
            flush=True,
        )

    def on_bal(b: BalanceUpdate) -> None:
        bump("balance_update")
        print(f"BAL    balance_raw={b.balance_raw}", flush=True)

    def on_margin(a: MarginAlert) -> None:
        bump("margin_alert")
        print(
            f"MARGIN symbol={a.symbol_id}  tier={a.tier}  ratio_bps={a.margin_ratio_bps}",
            flush=True,
        )

    def on_fund(fu: FundingRateUpdate) -> None:
        bump("funding_rate")
        print(
            f"FUND   symbol={fu.symbol_id}  "
            f"rate={fu.funding_rate}  last={fu.last_funding_rate}",
            flush=True,
        )

    def on_settle(s: SettlementUpdate) -> None:
        bump("settlement")
        print(f"SETTLE batch={s.batch_id}  status={s.status}", flush=True)

    def on_err(e: BaseException) -> None:
        non_fatal.append(str(e))

    client.on_order_update(on_order)
    client.on_position_update(on_pos)
    client.on_positions_snapshot(on_snap)
    client.on_system_health(on_health)
    client.on_balance_update(on_bal)
    client.on_margin_alert(on_margin)
    client.on_funding_rate_update(on_fund)
    client.on_settlement_update(on_settle)
    client.on_error(on_err)

    print("Connecting...")
    try:
        await client.connect()
    except Exception as e:
        print(f"Failed to connect: {e}", file=sys.stderr)
        return 1

    uid = client.user_uuid or ""
    print(f"Authenticated as user_uuid={uid}  (session encrypted)")

    try:
        await client.subscribe(["orders", "positions"])
    except Exception as e:
        print(f"Subscribe failed: {e}", file=sys.stderr)
        await client.disconnect()
        return 1

    print("Subscribed to order + position updates")
    await asyncio.sleep(0.35)

    # Leverage updates use encrypted REST on the HPKE SDK (not the WS client).
    print("Setting leverage to 1 via GodarkRestClient.update_leverage...")
    rest_kwargs = {k: v for k, v in client_kwargs.items() if k in ("api_key", "api_key_id", "api_secret", "passphrase", "user_uuid")}
    if edge:
        rest_kwargs["rest_base_url"] = edge.replace("wss://", "https://").replace(
            "ws://", "http://"
        ).removesuffix("/ws/v1")
    try:
        async with GodarkRestClient(**rest_kwargs) as rest:
            await rest.connect()
            lev_ack = await rest.update_leverage(SYMBOL, 1)
            print(
                f"update_leverage: success={lev_ack.success} order_id={lev_ack.order_id}"
            )
    except Exception as e:
        print_order_error("update_leverage rejected", e)

    def drain_orders(label: str) -> None:
        n = len(order_events)
        while order_events:
            u = order_events.popleft()
            badges = ""
            if u.cancel_reason is not None:
                badges += f"  cancel_reason={u.cancel_reason}"
            if u.reduce_only:
                badges += "  reduce_only=true"
            if u.post_only:
                badges += "  post_only=true"
            print(
                f"ORDER  {u.update_type}  id={u.order_id}  status={u.status}  "
                f"filled={u.filled_qty}  remaining={u.remaining_qty}{badges}",
                flush=True,
            )
        if n:
            print(f"  ({n} order update(s) {label})")

    mark = live_mark_price()
    buy_px = round(mark * 0.997, 1)
    print(f"Placing limit BUY @ {buy_px} (mark={mark})...")
    try:
        buy_ack = await client.place_order(
            SYMBOL,
            Side.BUY,
            OrderType.LIMIT,
            0.1,
            price=buy_px,
            time_in_force=TimeInForce.GTC,
        )
        print(f"BUY placed: order_id={buy_ack.order_id}  sequence={buy_ack.sequence}")
    except Exception as e:
        print_order_error("BUY rejected", e)
        await client.disconnect()
        return 1

    await asyncio.sleep(1)
    drain_orders("after BUY")

    modify_px = round(mark * 0.996, 1)
    print(f"Modifying order price to {modify_px}...")
    assert buy_ack is not None
    try:
        mod_ack = await client.modify_order(
            str(buy_ack.order_id), SYMBOL, new_price=modify_px
        )
        print(f"Modified: order_id={mod_ack.order_id}")
    except Exception as e:
        print_order_error("Modify rejected", e)

    await asyncio.sleep(1)
    drain_orders("after MODIFY")

    sell_px = round(mark * 1.03, 1)
    print(f"Placing limit SELL @ {sell_px}...")
    try:
        sell_ack = await client.place_order(
            SYMBOL,
            Side.SELL,
            OrderType.LIMIT,
            0.05,
            price=sell_px,
            time_in_force=TimeInForce.GTC,
        )
        print(f"SELL placed: order_id={sell_ack.order_id}")
        await asyncio.sleep(0.5)
        try:
            cack = await client.cancel_order(str(sell_ack.order_id), SYMBOL)
            print(f"SELL cancelled: order_id={cack.order_id}")
        except Exception as e:
            print_order_error("Cancel SELL rejected", e)
    except Exception as e:
        print_order_error("SELL rejected", e)

    await asyncio.sleep(1)
    drain_orders("after SELL/CANCEL")

    # --- Bulk quote (mass quote) -------------------------------------------
    # Place a whole ladder of resting quotes in a single batched request. With
    # the default (post_only) mode every leg is post-only: a leg that would
    # cross is rejected as "failed" so the batch fuses into one MPC round. Pass
    # post_only=False for the relaxed path, where a crossing leg takes liquidity
    # up to its limit and rests the remainder (reported per leg as fill_count).
    # Anchor the ladder/cross to the live BTC mark captured from the snapshot so
    # the crossing demo below is deterministic regardless of current price. Fall
    # back to GDX_BASE (default 64000) only if no mark was seen yet.
    base = last_mark.get("BTC") or float(os.environ.get("GDX_BASE", "64000"))
    print(f"Mass-quoting a 3-level BUY ladder (post-only), base={base:.2f}...")
    ladder = [
        {"side": Side.BUY, "price": round(base * (1 - 0.003), 1), "quantity": 0.02},
        {"side": Side.BUY, "price": round(base * (1 - 0.006), 1), "quantity": 0.02},
        {"side": Side.BUY, "price": round(base * (1 - 0.009), 1), "quantity": 0.02},
    ]
    resting_ids: list[int] = []
    try:
        mq = await client.mass_quote(SYMBOL, ladder)
        print(f"Mass quote: success={mq.success} sequence={mq.sequence} legs={len(mq.results)}")
        for r in mq.results:
            print(
                f"  leg {r.leg_index}: status={r.status} new_order_id={r.new_order_id} "
                f"fills={r.fill_count} err={r.error_code}",
                flush=True,
            )
            if r.status == "open" and r.new_order_id:
                resting_ids.append(int(r.new_order_id))
    except Exception as e:
        print_order_error("Mass quote rejected", e)

    await asyncio.sleep(1)
    drain_orders("after MASS QUOTE")

    if resting_ids:
        print(f"Batch-cancelling {len(resting_ids)} ladder orders (cleanup)...")
        try:
            bc = await client.batch_cancel(SYMBOL, resting_ids)
            for r in bc.results:
                print(
                    f"  cancel id={r.order_id}: cancelled={r.cancelled} err={r.error_code}",
                    flush=True,
                )
        except Exception as e:
            print_order_error("Batch cancel rejected", e)
        await asyncio.sleep(0.5)
        drain_orders("after BATCH CANCEL")

    # Demonstrate the batch-level post_only flag on a crossing leg. Price a BUY
    # ~5% above the live mark: aggressive enough to cross the resting ask, yet
    # within the exchange's 10%-of-oracle limit. Anchored to the live mark, this
    # makes the post_only=true (reject) vs false (fill) contrast deterministic.
    cross_px = round(base * 1.05, 1)
    # post_only=True: a crossing leg is rejected (would-cross, error_code 2018).
    print("Mass-quoting a crossing BUY with post_only=True (expect rejected/2018)...")
    try:
        mq = await client.mass_quote(
            SYMBOL, [{"side": Side.BUY, "price": cross_px, "quantity": 0.001}],
            post_only=True,
        )
        for r in mq.results:
            print(f"  leg {r.leg_index}: status={r.status} err={r.error_code} "
                  f"fills={r.fill_count}", flush=True)
    except Exception as e:
        print_order_error("post_only=True mass quote rejected", e)
    await asyncio.sleep(0.5)

    # post_only=False (relaxed): the crossing leg takes liquidity up to its limit
    # and rests the remainder; taker fills are reported per leg as fill_count.
    print("Mass-quoting a crossing BUY with post_only=False (expect filled, fill_count>0)...")
    try:
        mq = await client.mass_quote(
            SYMBOL, [{"side": Side.BUY, "price": cross_px, "quantity": 0.003}],
            post_only=False,
        )
        for r in mq.results:
            print(f"  leg {r.leg_index}: status={r.status} new_order_id={r.new_order_id} "
                  f"err={r.error_code} fills={r.fill_count}", flush=True)
    except Exception as e:
        print_order_error("post_only=False mass quote rejected", e)
    else:
        stray_ids = [
            int(r.new_order_id)
            for r in mq.results
            if r.status == "open" and r.new_order_id
        ]
        if stray_ids:
            print(f"Batch-cancelling {len(stray_ids)} post_only=False remainder(s)...")
            try:
                bc = await client.batch_cancel(SYMBOL, stray_ids)
                for r in bc.results:
                    print(
                        f"  cancel id={r.order_id}: cancelled={r.cancelled} err={r.error_code}",
                        flush=True,
                    )
            except Exception as e:
                print_order_error("post_only=False remainder cancel rejected", e)
    await asyncio.sleep(1)
    drain_orders("after post_only mass quotes")

    print("Cancelling original BUY (cleanup)...")
    try:
        await client.cancel_order(str(buy_ack.order_id), SYMBOL)
        print("Original BUY cancelled")
    except Exception:
        print("Original BUY already filled or cancelled")

    await asyncio.sleep(0.35)

    print(sep)
    print("  Session complete")
    print(
        "  Callback push counts:",
        f"orders={counts['order_update']} positions={counts['position_update']} "
        f"snapshots={counts['positions_snapshot']} health={counts['system_health']} "
        f"balance={counts['balance_update']} margin={counts['margin_alert']} "
        f"funding={counts['funding_rate']} settle={counts['settlement']}",
        flush=True,
    )
    for msg in non_fatal:
        print(f"SDK ERROR (non-fatal): {msg}")
    print(f"  Non-fatal callbacks: {len(non_fatal)}")
    print(sep)

    await client.disconnect()
    print("Disconnected cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
