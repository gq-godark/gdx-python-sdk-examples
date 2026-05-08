# GoDark Python SDK Reference (MM distribution)

This repo focuses on encrypted WebSocket trading via `godark.GodarkClient`.
REST adapters and standalone market-data clients exist in the full upstream
repository but are not used by these MM-facing examples.

Order placement illustrated here is **`MARKET`** and **`LIMIT`**.

## Quick start

```python
import asyncio
import os

from godark import GodarkClient, OrderType, Side, TimeInForce

async def main():
    async with GodarkClient(
        api_key_id=os.environ["GODARK_API_KEY_ID"],
        api_secret=os.environ["GODARK_API_SECRET"],
        base_url=os.environ.get("GODARK_EDGE_URL", "wss://api.godark-dex.com"),
    ) as client:
        ack = await client.place_order(
            "BTC-USDC-PERP",
            Side.SELL,
            OrderType.LIMIT,
            0.01,
            price=999_999.0,
            time_in_force=TimeInForce.GTC,
        )
        await client.cancel_order(ack.order_id, "BTC-USDC-PERP")

asyncio.run(main())
```

(`base_url=` may omit the `/ws/v1` suffix — the SDK appends it.)

## Configuration

| Env var | Meaning |
|---------|---------|
| `GODARK_API_KEY_ID` | Required (`gdk_…`) |
| `GODARK_API_SECRET` | Required plaintext secret |
| `GODARK_EDGE_URL` | Optional WebSocket origin (default `wss://api.godark-dex.com`) |
| `GODARK_USER_UUID` | Optional fallback UUID when auth responses omit user id |

## `GodarkClient` API

See `examples/full_trader_example.py` for a complete integration pattern.

### Lifecycle

| Method | Purpose |
|--------|---------|
| `async connect()` | Authenticate API key + establish ECDH + AES-GCM session |
| `async disconnect()` | Close transport |
| `async __aenter__` / `__aexit__` | Async-context convenience (`async with GodarkClient(...) as c:`) |
| `@property user_uuid` | UUID string after authentication |

### Trading commands

| Method | Purpose |
|--------|---------|
| `async place_order(symbol, side, order_type, quantity, price?, ...)` → `OrderAck` | Sends encrypted place |
| `async cancel_order(order_id, symbol)` | Cancel by numeric id |
| `async modify_order(order_id, symbol, new_price?, new_quantity?)` | Amend working order |

**Concurrency:** mimic one command in flight — `place_market/cancel/modify`
share a single acknowledgement slot internally.

### Subscriptions

| Method | Meaning |
|--------|---------|
| `async subscribe(("orders","positions"))` | Private channel subscriptions |
| `async unsubscribe(...)` | Unsubscribe subset |

Exact channel strings follow the docs wire `{channel: …}` payloads; subscribe
according to upstream edge documentation when enabling additional sequencer
streams.

### Push streams (callbacks + async iterators)

Register simple callbacks **before** `connect()` finishes if you must capture the
opening burst; combine with iterators for async loops.

**Callbacks**

| Registrar | Payload type |
|-----------|----------------|
| `on_order_update(cb)` | `OrderUpdate` |
| `on_position_update(cb)` | `PositionUpdate` |
| `on_positions_snapshot(cb)` | `PositionsSnapshot` |
| `on_system_health(cb)` | `SystemHealthUpdate` |
| `on_balance_update(cb)` | `BalanceUpdate` |
| `on_margin_alert(cb)` | `MarginAlert` |
| `on_funding_rate_update(cb)` | `FundingRateUpdate` |
| `on_settlement_update(cb)` | `SettlementUpdate` |
| `on_reconnect(cb)` | `()` |
| `on_error(cb)` | `BaseException` (non-fatal decrypt/parse/connect errors only) |

**Async iterators**

| Method | Yield type |
|--------|------------|
| `order_updates()` | `OrderUpdate` |
| `position_updates()` | `PositionUpdate` |
| `positions_snapshots()` | `PositionsSnapshot` |
| `system_health_updates()` | `SystemHealthUpdate` |
| `balance_updates()` | `BalanceUpdate` |
| `margin_alerts()` | `MarginAlert` |
| `funding_rate_updates()` | `FundingRateUpdate` |
| `settlement_updates()` | `SettlementUpdate` |

Queues are bounded and drop oldest on overflow (`stream_buffer_size` ctor arg).

| Push payload | Highlights | Typical MM use |
|--------------|-----------|----------------|
| `PositionsSnapshot` | `rows: tuple[PositionRow, …]` with mark / uPnL, `source` (`INITIAL`/`PERIODIC`/`EVENT`) | Hydrate + refresh blotters |
| `SystemHealthUpdate` | `accepting_orders`, MPC tallies | Pause quoting when cluster unhealthy |
| `BalanceUpdate` | `shielded_balance_raw` integer | Equity widget |
| `MarginAlert` | `tier`, ratios, `recovered` banner | Margin warnings |
| `FundingRateUpdate` | Funding forecast per `symbol_id` | Curve display |
| `SettlementUpdate` | `batch_id`, `SettlementBatchStatus`, Solana sig | Settlement reconciliation |

## Core types (`godark.types`)

| Type | Notes |
|------|-------|
| `OrderAck` | `order_id`, `sequence`, booleans/strings for reject metadata |
| `OrderUpdate` | Includes optional `realized_pnl`, `leverage` |
| `PositionUpdate` | Includes `PositionUpdateType.FUNDING_APPLIED` mapping |
| `PositionRow`, `PositionsSnapshot`, `PositionsSnapshotSource` | Batch positions |
| `SystemHealthUpdate`, `BalanceUpdate`, `MarginAlert`, `FundingRateUpdate`, `SettlementUpdate`, `SettlementBatchStatus` | Sequencer pushes |
| `UnknownSequencerPush` | Fallback when an unknown protobuf `oneof` arrives |

## Enums (`godark.enums`)

Important values: `Side`, `OrderType`, `OrderStatus`, `TimeInForce`,
`OrderUpdateType`, `PositionUpdateType`, `CancelReason`,
`PositionsSnapshotSource`, `SettlementBatchStatus`.

## Errors

Hierarchy:

```text
GodarkError
├── AuthenticationError
├── SessionError
├── OrderError        # rejects include optional symbolic `error_code`
├── ConnectionError   # transport / not-connected guard
├── EncryptionError   # cryptographic failures
└── TimeoutError      # command stalled waiting for acknowledgement
```

`OrderError` gains symbolic names wherever the sequencer returns a canonical
numeric `error_code` (see `godark.order_error_code.ORDER_ERROR_CODES` upstream).

## Helpers

| Helper | Meaning |
|--------|---------|
| `make_order_error_from_code(int)` | Numeric ack → richer `OrderError` |
| `make_order_error_from_json(reason?, code?)` | JSON acknowledgement path |

## Distribution files

| File | Role |
|------|------|
| `examples/quickstart.py` | Fast smoke (`LIMIT` placement + cancel) |
| `examples/full_trader_example.py` | Full push surface + richer flow |
| `examples/dotenv.py` | Lightweight `.env` loader + rejection printer |
| `scripts/setup_pypy.sh` | `pip install ./sdk` into `.venv-pypy` |

## SDK layout (`sdk/`)

```
sdk/
├── pyproject.toml           # Hatch metadata (no optional dev bundles)
├── README.md
├── shared/symbols.json
└── godark/
    ├── __init__.py
    ├── client.py            # pushes + ECDH encrypted trading
    ├── order_error_code.py # symbolic error table
    ├── types.py
    ├── enums.py
    └── _generated/…         # committed `*_pb2.py` stubs
```

Refresh internally:

```bash
./scripts/refresh_sdk.sh /path/to/gdx-python-sdk
```
