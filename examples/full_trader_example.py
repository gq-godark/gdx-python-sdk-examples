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

from dotenv import load_dotenv, print_order_error
from godark import (
    BalanceUpdate,
    FundingRateUpdate,
    GodarkClient,
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


async def main() -> int:
    load_dotenv()
    sep = "=" * 60
    print(sep)
    print("  GoDark Python SDK — Trader Reference Example")
    print(sep)
    print("Order-type support in this distribution: MARKET, LIMIT")

    api_key_id = os.environ.get("GODARK_API_KEY_ID", "").strip()
    api_secret = os.environ.get("GODARK_API_SECRET", "").strip()
    passphrase = os.environ.get("GODARK_PASSPHRASE", "").strip()
    if not api_key_id or not api_secret or not passphrase:
        print(
            "Missing GODARK_API_KEY_ID / GODARK_API_SECRET / GODARK_PASSPHRASE "
            "(.env at repo root).",
            file=sys.stderr,
        )
        return 1

    base_url = (
        os.environ.get("GODARK_EDGE_URL", "").strip()
        or "wss://api.godark-dex.com"
    )
    print(f"Endpoint: {base_url}")

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

    client = GodarkClient(
        api_key_id=api_key_id,
        api_secret=api_secret,
        passphrase=passphrase,
        base_url=base_url,
        transport=transport,
    )

    def on_order(u: OrderUpdate) -> None:
        bump("order_update")
        order_events.append(u)

    def on_pos(u: PositionUpdate) -> None:
        bump("position_update")
        print(
            f"POS    side={u.side}  size={u.size}  entry={u.entry_price}",
            flush=True,
        )

    def on_snap(s: PositionsSnapshot) -> None:
        bump("positions_snapshot")
        print(
            f"SNAP   source={s.source}  rows={len(s.rows)}  ts={s.server_timestamp}",
            flush=True,
        )
        for row in s.rows:
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
        print(f"BAL    shielded_raw={b.shielded_balance_raw}", flush=True)

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
            f"current={fu.current_rate}  predicted={fu.predicted_rate}",
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

    def drain_orders(label: str) -> None:
        n = len(order_events)
        while order_events:
            u = order_events.popleft()
            print(
                f"ORDER  {u.update_type}  id={u.order_id}  status={u.status}  "
                f"filled={u.filled_qty}  remaining={u.remaining_qty}",
                flush=True,
            )
        if n:
            print(f"  ({n} order update(s) {label})")

    print("Placing limit BUY @ 67500...")
    try:
        buy_ack = await client.place_order(
            SYMBOL,
            Side.BUY,
            OrderType.LIMIT,
            0.1,
            price=67_500.0,
            time_in_force=TimeInForce.GTC,
        )
        print(f"BUY placed: order_id={buy_ack.order_id}  sequence={buy_ack.sequence}")
    except Exception as e:
        print_order_error("BUY rejected", e)
        await client.disconnect()
        return 1

    await asyncio.sleep(1)
    drain_orders("after BUY")

    print("Modifying order price to 68000...")
    assert buy_ack is not None
    try:
        mod_ack = await client.modify_order(
            str(buy_ack.order_id), SYMBOL, new_price=68_000.0
        )
        print(f"Modified: order_id={mod_ack.order_id}")
    except Exception as e:
        print_order_error("Modify rejected", e)

    await asyncio.sleep(1)
    drain_orders("after MODIFY")

    print("Placing limit SELL @ 95000...")
    try:
        sell_ack = await client.place_order(
            SYMBOL,
            Side.SELL,
            OrderType.LIMIT,
            0.05,
            price=95_000.0,
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
