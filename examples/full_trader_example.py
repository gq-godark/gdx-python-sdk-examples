#!/usr/bin/env python3
"""GoDark Python SDK — trader reference example (parity with other MM distributions).

Registers callbacks for orders, positions, and sequencer push streams, then
places post-only limits priced from the live mark and cancels only those orders.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict, deque

from dotenv import get_first, load_dotenv, print_order_error
from godark import (
    BalanceUpdate,
    Environment,
    FundingRateUpdate,
    GodarkClient,
    GodarkRestClient,
    LeverageSettings,
    MarginAlert,
    OrderType,
    OrderUpdate,
    PlaceOrderOptions,
    PositionUpdate,
    PositionsSnapshot,
    SettlementUpdate,
    Side,
    SystemHealthUpdate,
    TimeInForce,
    TransportConfig,
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
    sep = "=" * 60
    print(sep)
    print("  GoDark Python SDK — Trader Reference Example")
    print(sep)
    print("Sample orders are post-only LIMIT, priced from the live mark")

    legacy_key = get_first("GODARK_API_KEY", "GDX_API_KEY")
    edge = get_first("GODARK_EDGE_URL", "GDX_EDGE_URL")
    print(f"Endpoint: {edge or Environment.TESTNET.edge_base_url}")

    transport = TransportConfig(
        additional_headers={"X-Trader-Tag": "python-full-trader-demo"},
        open_timeout=10.0,
        command_timeout=10.0,
        heartbeat_interval=30.0,
        stale_timeout=120.0,
        missed_heartbeat_limit=2,
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
        if account := get_first("GODARK_ACCOUNT", "GDX_ACCOUNT"):
            client_kwargs["account"] = account
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
    rest_kwargs = {
        k: client_kwargs[k]
        for k in ("api_key", "api_key_id", "api_secret", "passphrase", "account", "user_uuid")
        if k in client_kwargs
    }
    if edge:
        rest_kwargs["rest_base_url"] = edge
    rest = GodarkRestClient(**rest_kwargs)

    def on_order(u: OrderUpdate) -> None:
        bump("order_update")
        order_events.append(u)

    def on_pos(u: PositionUpdate) -> None:
        bump("position_update")
        print(
            f"POS    side={u.side}  size={u.size}  entry={u.entry_price}",
            flush=True,
        )

    # Capture a BTC mark from an authenticated positions snapshot when a row
    # actually includes one. A flat account has no mark here; pricing then uses
    # public open interest. Keep the venue decimal string (no float→str).
    last_mark: dict[str, str] = {}

    def on_snap(s: PositionsSnapshot) -> None:
        bump("positions_snapshot")
        print(
            f"SNAP   source={s.source}  rows={len(s.rows)}  ts={s.server_timestamp}",
            flush=True,
        )
        for row in s.rows:
            if row.symbol_id == 1 and row.mark_price:
                last_mark["BTC"] = row.mark_price
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

    def on_lev(ls: LeverageSettings) -> None:
        bump("leverage_settings")
        rows = ", ".join(f"{r.symbol_id}={r.leverage}x" for r in ls.settings[:5])
        suffix = "..." if len(ls.settings) > 5 else ""
        print(f"LEVERAGE settings=[{rows}{suffix}]", flush=True)

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
    client.on_leverage_settings(on_lev)
    client.on_error(on_err)

    print("Connecting...")
    try:
        await client.connect()
    except Exception as e:
        print(f"Failed to connect: {e}", file=sys.stderr)
        await rest.disconnect()
        return 1

    account = client.account or ""
    print(f"Authenticated as account={account}  (session encrypted)")

    try:
        await client.subscribe(["orders", "positions", "funding_rate"])
    except Exception as e:
        print(f"Subscribe failed: {e}", file=sys.stderr)
        await client.disconnect()
        await rest.disconnect()
        return 1

    print("Subscribed to order + position updates")
    await asyncio.sleep(0.35)

    # Leverage updates use encrypted WebSocket (same session as place/cancel).
    print("Setting leverage to 1 via GodarkClient.update_leverage...")
    try:
        lev_ack = await client.update_leverage(SYMBOL, 1)
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

    class _MarkRow:
        def __init__(self, symbol_id: int, mark_price: str) -> None:
            self.symbol_id = symbol_id
            self.mark_price = mark_price

    own_ids: list[str] = []
    baseline: dict = {}
    mark = ""
    failed = False
    try:
        _, positions = await read_book(rest)
        baseline = position_fingerprint(positions)
        snap_rows = [_MarkRow(1, last_mark["BTC"])] if last_mark.get("BTC") else positions.rows
        mark = await resolve_live_mark(rest, position_rows=snap_rows)
    except Exception as e:
        print(f"No live mark; placing nothing: {e}", file=sys.stderr)
        failed = True

    async def place_post_only(side: Side, price: str, label: str) -> str:
        ack = await client.place_order(
            SYMBOL,
            side,
            OrderType.LIMIT,
            QTY,
            price=price,
            time_in_force=TimeInForce.GTC,
            options=PlaceOrderOptions(post_only=True),
        )
        if not ack.success or not ack.order_id:
            raise RuntimeError(f"{label} place failed: {ack.error or ack.error_code}")
        oid = str(ack.order_id)
        own_ids.append(oid)
        print(f"{label} placed: order_id={oid} @ {price}")
        return oid

    if not failed:
        buy_id = ""
        try:
            buy_px = post_only_price(mark, Side.BUY, steps=1)
            print(f"Placing post-only BUY @ {buy_px} (mark={mark})...")
            buy_id = await place_post_only(Side.BUY, buy_px, "BUY")
            await asyncio.sleep(1)
            drain_orders("after BUY")
            modify_px = post_only_price(mark, Side.BUY, steps=2)
            print(f"Modifying order price to {modify_px}...")
            mod_ack = await client.modify_order(buy_id, SYMBOL, new_price=modify_px)
            if not mod_ack.success:
                raise RuntimeError(f"modify failed: {mod_ack.error or mod_ack.error_code}")
            print(f"Modified: order_id={mod_ack.order_id}")
            drain_orders("after MODIFY")
            await cancel_own(client, buy_id)
            print(f"BUY cancelled: order_id={buy_id}")
            own_ids.remove(buy_id)
            await assert_own_orders_flat(rest, {buy_id}, baseline)
            drain_orders("after BUY cancel")

            sell_px = post_only_price(mark, Side.SELL, steps=1)
            print(f"Placing post-only SELL @ {sell_px}...")
            sell_id = await place_post_only(Side.SELL, sell_px, "SELL")
            drain_orders("after SELL")
            await cancel_own(client, sell_id)
            print(f"SELL cancelled: order_id={sell_id}")
            own_ids.remove(sell_id)
            await assert_own_orders_flat(rest, {sell_id}, baseline)
            drain_orders("after SELL/CANCEL")

            print(f"Mass-quoting a 3-level post-only BUY ladder, mark={mark}...")
            ladder = [
                {"side": Side.BUY, "price": post_only_price(mark, Side.BUY, steps=1), "quantity": QTY},
                {"side": Side.BUY, "price": post_only_price(mark, Side.BUY, steps=2), "quantity": QTY},
                {"side": Side.BUY, "price": post_only_price(mark, Side.BUY, steps=3), "quantity": QTY},
            ]
            mq = await client.mass_quote(SYMBOL, ladder, post_only=True)
            print(f"Mass quote: success={mq.success} sequence={mq.sequence} legs={len(mq.results)}")
            for r in mq.results:
                print(
                    f"  leg {r.leg_index}: status={r.status} new_order_id={r.new_order_id} "
                    f"fills={r.fill_count} err={r.error_code}",
                    flush=True,
                )
                if r.new_order_id and r.status == "open":
                    own_ids.append(str(r.new_order_id))
                if r.status != "open" or not r.new_order_id or r.fill_count:
                    raise RuntimeError(f"mass-quote leg {r.leg_index} did not rest post-only")
            if not mq.success:
                raise RuntimeError("mass quote rejected")
            drain_orders("after MASS QUOTE")
            print(f"Cancelling {len(own_ids)} ladder order(s) by id...")
            ladder_ids = list(own_ids)
            await asyncio.sleep(1)
            for oid in ladder_ids:
                ack = await client.cancel_order(oid, SYMBOL)
                if not ack.success:
                    raise RuntimeError(f"cancel failed for {oid}: {ack.error or ack.error_code}")
                print(f"  cancel order_id={ack.order_id}", flush=True)
                own_ids.remove(oid)
            await assert_own_orders_flat(rest, set(ladder_ids), baseline)
            drain_orders("after ladder cancel")
        except Exception as e:
            print_order_error("Trading failed", e)
            failed = True
            for oid in list(own_ids):
                try:
                    await cancel_own(client, oid)
                    own_ids.remove(oid)
                except Exception as cancel_err:
                    print_order_error(f"cancel {oid} rejected", cancel_err)
            if mark:
                try:
                    await flatten_positions(client, rest, baseline, mark)
                except Exception as flat_err:
                    print(f"Flatten failed: {flat_err}", file=sys.stderr)

    if not failed and own_ids:
        print(f"Leftover own orders: {own_ids}", file=sys.stderr)
        failed = True

    print(sep)
    print("  Session complete")
    print(
        "  Callback push counts:",
        f"orders={counts['order_update']} positions={counts['position_update']} "
        f"snapshots={counts['positions_snapshot']} health={counts['system_health']} "
        f"balance={counts['balance_update']} margin={counts['margin_alert']} "
        f"funding={counts['funding_rate']} settle={counts['settlement']} "
        f"leverage={counts['leverage_settings']}",
        flush=True,
    )
    for msg in non_fatal:
        print(f"SDK ERROR (non-fatal): {msg}")
    print(f"  Non-fatal callbacks: {len(non_fatal)}")
    print(sep)

    await client.disconnect()
    await rest.disconnect()
    print("Disconnected cleanly")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
