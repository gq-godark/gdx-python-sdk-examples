# GoDark Python SDK Reference

This reference describes the API and workflow used by the market-maker-facing
distribution in this repository.

The MM examples use WebSocket encrypted trading via `godark.GodarkClient`.
Encrypted REST trading is not supported — all order flow (place / modify /
cancel / mass-quote) runs over the Noise XK WebSocket client. Standalone
market-data examples are excluded from this distribution.

Order placement support in this MM distribution is limited to `MARKET` and
`LIMIT`.

## Quick Start

```python
import asyncio
from godark import GodarkClient, OrderType, Side

async def main():
    async with GodarkClient(
        api_key_id="gdk_...",
        api_secret="...",
        base_url="wss://api.godark-dex.com",  # optional override
    ) as client:
        ack = await client.place_order(
            "BTC-USDC-PERP", Side.SELL, OrderType.LIMIT, 0.01, price=999_999.0,
        )
        await client.cancel_order(ack.order_id, "BTC-USDC-PERP")

asyncio.run(main())
```

## Configuration

The MM examples expect:

- `GODARK_API_KEY_ID` (required)
- `GODARK_API_SECRET` (required)
- `GODARK_PASSPHRASE` (required for API key-pair auth)
- `GDX_NOISE_STATIC_PUBLIC_KEY` (required for encrypted WebSocket trading) — 64 hex chars; aliases `GDX_NOISE_STATIC_PUBKEY`, `GODARK_NOISE_STATIC_PUBLIC_KEY`
- `GODARK_EDGE_URL` (optional, defaults to `wss://api.godark-dex.com`)

Use `.env.example` as the template for your local `.env`.

## GodarkClient API

**Module:** `godark` (re-exports `GodarkClient` from `godark.client`)

### Core lifecycle

| Method | Signature | Purpose |
|--------|-----------|---------|
| `connect` | `async def connect() -> None` | Authenticate and establish Noise XK encrypted session |
| `disconnect` | `async def disconnect() -> None` | Graceful disconnect |
| `logout` | `async def logout() -> None` | Logout and disconnect |
| `__aenter__` / `__aexit__` | `async with GodarkClient(...) as c:` | Async-context wrapper around `connect()` / `disconnect()` |
| `user_uuid` | `@property -> str \| None` | Authenticated user id |

### Trading commands

| Method | Signature | Purpose |
|--------|-----------|---------|
| `place_order` | `async def place_order(symbol, side, order_type, quantity, price=None, time_in_force="GTC") -> OrderAck` | Place encrypted order |
| `cancel_order` | `async def cancel_order(order_id, symbol) -> OrderAck` | Cancel order |
| `modify_order` | `async def modify_order(order_id, symbol, new_price=None, new_quantity=None) -> OrderAck` | Modify order |

`side`, `order_type`, and `time_in_force` accept either the typed enum
(`Side.SELL`) or its string name (`"SELL"`).

### Streams

| Method | Signature | Purpose |
|--------|-----------|---------|
| `subscribe` | `async def subscribe(channels) -> None` | Subscribe to private channels (`orders`, `positions`) |
| `unsubscribe` | `async def unsubscribe(channels) -> None` | Unsubscribe |
| `order_updates()` | `async def order_updates() -> AsyncIterator[OrderUpdate]` | Async pull from order queue |
| `position_updates()` | `async def position_updates() -> AsyncIterator[PositionUpdate]` | Async pull from position queue |

### Callbacks

```python
client.on_order_update(lambda u: ...)
client.on_position_update(lambda u: ...)
client.on_reconnect(lambda: ...)
client.on_error(lambda e: ...)
```

The Python SDK uses **registrar methods** — each `on_*` call appends to an
internal list, so multiple subscribers are allowed.

In addition, the SDK surfaces every other push the sequencer can emit on the
trading WebSocket. Each one has a matching `on_*` registrar **and** a matching
`async def` iterator (the Python equivalent of C++'s `try_recv_*()` queues):

```python
client.on_positions_snapshot(on_snapshot)
client.on_system_health(on_health)
client.on_balance_update(on_balance)
client.on_margin_alert(on_margin)
client.on_funding_rate_update(on_funding)
client.on_settlement_update(on_settle)
```

| Push                  | Field highlights                                                                                | Typical use                                          |
|-----------------------|-------------------------------------------------------------------------------------------------|------------------------------------------------------|
| `PositionsSnapshot`   | `rows[]` (`PositionRow{symbol_id, side, size, entry_price, mark_price, unrealized_pnl, ...}`), `source` (`INITIAL` / `PERIODIC` / `EVENT`) | Hydrate the open-positions table on connect; refresh every ~5s. |
| `SystemHealthUpdate`  | `total_nodes`, `ready`, `degraded`, `accepting_orders`                                          | Display node-cluster status; pause submissions if `accepting_orders is False`. |
| `BalanceUpdate`       | `shielded_balance_raw` (raw lamports-style integer)                                             | Refresh the wallet/equity widget after each fill or settlement. |
| `MarginAlert`         | `symbol_id`, `tier`, `margin_ratio_bps`, `liquidation_price_bps`, `recovered`                   | Show / clear the margin-tier banner per `(owner, symbol_id)`. |
| `FundingRateUpdate`   | `symbol_id`, `current_rate`, `predicted_rate`, `next_funding_time`                              | Update funding ticker / book metadata.               |
| `SettlementUpdate`    | `batch_id`, `status` (`SUBMITTED` / `CONFIRMED` / `FAILED`), `tx_signature`, `affected_user_uuids[]` | Reconcile settled batches, surface Solana tx links.  |

Each push has a single bounded `asyncio.Queue` (default 256). On overflow the
oldest item is dropped and a warning is logged. Both the iterator and the
matching `on_*` callback fire for the same item.

### Concurrency rule

Only one command (`place_order`, `cancel_order`, `modify_order`) should be in
flight at a time. `await` each call before issuing the next.

## Core Types

**Module:** `godark.types`

All types are frozen `@dataclass` instances. Numeric values are returned as
strings to preserve sequencer-side decimal precision.

### OrderAck

- `order_id` (`str`)
- `success` (`bool`)
- `sequence` (`str`)
- `error_code` (`str | None`)
- `error` (`str | None`)

### OrderUpdate

Includes order lifecycle fields such as:
`order_id`, `symbol_id`, `side`, `status`, `update_type`,
`price`, `quantity`, `filled_qty`, `remaining_qty`, `timestamp`.

### PositionUpdate

Includes position lifecycle fields such as:
`user_uuid`, `symbol_id`, `side`, `update_type`,
`size`, `entry_price`, `fill_price`, `fill_qty`, `timestamp`.

## Enums

**Module:** `godark.enums`

All enums inherit from `str, Enum` (e.g. `Side.SELL == "SELL"`).

- `Side`: `BUY`, `SELL`
- `OrderType`: `MARKET`, `LIMIT`, `PEG_TO_MID`, `PEG_TO_BID`, `PEG_TO_ASK`
- `TimeInForce`: `GTC`, `IOC`, `FOK`, `GTD`
- `OrderStatus`: `NEW`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`
- `OrderUpdateType`: `OPEN`, `FILLED`, `PARTIALLY_FILLED`, `CANCELLED`, `REJECTED`, `MODIFIED`, `CANCEL_REJECTED`, `MODIFY_REJECTED`
- `PositionUpdateType`: `SNAPSHOT`, `OPEN`, `INCREASE`, `DECREASE`, `CLOSE`, `FUNDING_APPLIED`
- `CancelReason`: `USER_REQUESTED`, `IOC_REMAINDER`, `FOK_NOT_FILLED`, `EXPIRED`, `SYSTEM`

Note: the SDK enum includes additional order types for compatibility, but this
MM distribution supports placing only `MARKET` and `LIMIT` orders.

## Errors

**Module:** `godark.errors`

All SDK exceptions inherit from `godark.GodarkError`:

- `AuthenticationError`
- `SessionError`
- `OrderError` — also carries `error_code: str | None` with the symbolic reason
  (e.g. `"PRICE_DEVIATION_TOO_LARGE"`, `"MARGIN_INSUFFICIENT"`). See
  `quickstart.py` for the catch-and-print pattern.
- `ConnectionError`
- `EncryptionError`
- `TimeoutError`

## Example files in this distribution

| File | Purpose |
|------|---------|
| `examples/quickstart.py` | Minimal connect, place, cancel |
| `examples/full_trader_example.py` | Reference bot flow: callbacks, place / modify / cancel, mass-quote / batch-cancel |

## pip integration

```bash
pip install wheels/godark-*.whl
```

```python
from godark import GodarkClient, OrderType, Side, TimeInForce
```
