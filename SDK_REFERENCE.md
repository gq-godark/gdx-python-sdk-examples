# GoDark Python SDK Reference (MM Distribution)

This reference describes the API and workflow used by the market-maker-facing
distribution in this repository.

The MM examples use WebSocket encrypted trading via `godark.GodarkClient`.
REST adapters and standalone market-data clients exist in the upstream SDK but
are intentionally excluded from this distribution.

Order placement support in this MM distribution is limited to `MARKET` and
`LIMIT`.

## Quick Start

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

`base_url=` may omit the `/ws/v1` suffix — the SDK appends it.

## Configuration

The MM examples expect:

- `GODARK_API_KEY_ID` (required)
- `GODARK_API_SECRET` (required)
- `GODARK_EDGE_URL` (optional, defaults to `wss://api.godark-dex.com`)
- `GODARK_USER_UUID` (optional fallback when the auth response omits a user id; some local edges need this)

Use `.env.example` as the template for your local `.env`. The `examples/dotenv.py`
helper loads it from the repo root; OS environment variables win over `.env` values.

## GodarkClient API

**Module:** `godark` (re-exports `GodarkClient` from `godark.client`)

### Core lifecycle

| Method | Signature | Purpose |
|--------|-----------|---------|
| `connect` | `async def connect() -> None` | Authenticate and establish Noise XK encrypted WebSocket session |
| `disconnect` | `async def disconnect() -> None` | Graceful disconnect; cancels pending reconnect tasks |
| `logout` | `async def logout() -> None` | Send docs `op: logout` when supported, then disconnect |
| `__aenter__` / `__aexit__` | `async with GodarkClient(...) as c:` | Async-context wrapper around `connect()` / `disconnect()` |
| `user_uuid` | `@property -> str \| None` | Authenticated user id (set after `connect`) |
| `account_id` | `@property -> str \| None` | Docs `op: login` account identifier when supplied by the edge |
| `login_session_id` | `@property -> str \| None` | Docs `op: login` session identifier when supplied by the edge |
| `token_expires_at` | `@property -> str \| None` | Docs `op: login` token expiry timestamp when supplied by the edge |
| `cancel_on_disconnect` | `@property -> bool` | Effective docs `cancel_on_disconnect` setting for this socket |

`GodarkClient.__init__` keyword arguments:

- `api_key_id`, `api_secret` — required pair (or single `api_key="<id>:<secret>"` token).
- `base_url` — host-only WebSocket origin; SDK appends `/ws/v1`. Falls back to `GODARK_EDGE_URL` / `GDX_EDGE_URL` then production.
- `user_uuid` — fallback used when the edge auth response omits a user id; falls back to `GODARK_USER_UUID` / `GDX_USER_UUID`.
- `noise_static_public_key_hex` — pinned sequencer Noise static key (64 hex); defaults to `GDX_NOISE_STATIC_PUBLIC_KEY` and aliases.
- `auto_reconnect=True` — automatically reconnect after transport drops.
- `symbol_map=None` — override the default symbol-name → numeric-id table.
- `transport=None` — `TransportConfig` for TLS, headers, timeouts, heartbeats.
- `stream_buffer_size=256` — bound for every push queue (see *Async iterators* below).

### Trading commands

| Method | Signature | Purpose |
|--------|-----------|---------|
| `place_order` | `async def place_order(symbol, side, order_type, quantity, price=None, time_in_force="GTC", aon=False, min_fill_size=None, expiry_time=None) -> OrderAck` | Place encrypted order; raises `OrderError` on rejection |
| `cancel_order` | `async def cancel_order(order_id: str, symbol: str = "BTC-USDC-PERP") -> OrderAck` | Cancel by numeric id (passed as string) |
| `modify_order` | `async def modify_order(order_id: str, symbol="BTC-USDC-PERP", new_price=None, new_quantity=None) -> OrderAck` | Amend price and/or quantity of a working order |

`side`, `order_type`, and `time_in_force` accept either the typed enum
(`Side.SELL`) or its string name (`"SELL"`). `order_id` is a string but parses
as an integer internally — pass `str(ack.order_id)` rather than the dataclass
field bare if you re-stringify it.

### Streams (subscribe / unsubscribe)

| Method | Signature | Purpose |
|--------|-----------|---------|
| `subscribe` | `async def subscribe(channels=("orders", "positions")) -> None` | Subscribe to private push channels |
| `unsubscribe` | `async def unsubscribe(channels=("orders", "positions")) -> None` | Unsubscribe a subset |

Exact channel strings match the docs wire `{channel: …}` payloads; subscribe
according to upstream edge documentation when enabling additional sequencer
streams.

### Callbacks

Register callbacks **before** `connect()` finishes if you must capture the
opening burst of pushes:

```python
client.on_order_update(lambda u: print("ORDER", u.order_id, u.status))
client.on_position_update(lambda u: print("POS", u.side, u.size))
client.on_reconnect(lambda: print("reconnected"))
client.on_error(lambda e: print("non-fatal:", e))
```

Unlike the C++ SDK (where each `on_*` is an assignable function-object slot),
the Python SDK uses **registrar methods** — each `on_*` call appends to an
internal list, so multiple subscribers are allowed.

In addition to `on_order_update` / `on_position_update`, the SDK surfaces every
other push the sequencer can emit on the trading WebSocket:

```python
client.on_positions_snapshot(on_snapshot)
client.on_system_health(on_health)
client.on_balance_update(on_balance)
client.on_margin_alert(on_margin)
client.on_funding_rate_update(on_funding)
client.on_settlement_update(on_settle)
```

| Registrar | Payload |
|-----------|---------|
| `on_order_update(cb)` | `OrderUpdate` |
| `on_position_update(cb)` | `PositionUpdate` |
| `on_positions_snapshot(cb)` | `PositionsSnapshot` |
| `on_system_health(cb)` | `SystemHealthUpdate` |
| `on_balance_update(cb)` | `BalanceUpdate` |
| `on_margin_alert(cb)` | `MarginAlert` |
| `on_funding_rate_update(cb)` | `FundingRateUpdate` |
| `on_settlement_update(cb)` | `SettlementUpdate` |
| `on_reconnect(cb)` | `()` (no payload) |
| `on_error(cb)` | `BaseException` (non-fatal decrypt / parse / push-routing errors) |

### Async iterators

For each push above (except `on_reconnect` / `on_error`), there's a matching
`async def` iterator. These are the Python equivalent of C++'s
`std::optional<T> try_recv_*()` queues — same bounded buffer, but yielded via
`async for` instead of polled:

```python
async for u in client.order_updates():
    print(u.order_id, u.status)
```

| Iterator | Yields |
|----------|--------|
| `order_updates()` | `OrderUpdate` |
| `position_updates()` | `PositionUpdate` |
| `positions_snapshots()` | `PositionsSnapshot` |
| `system_health_updates()` | `SystemHealthUpdate` |
| `balance_updates()` | `BalanceUpdate` |
| `margin_alerts()` | `MarginAlert` |
| `funding_rate_updates()` | `FundingRateUpdate` |
| `settlement_updates()` | `SettlementUpdate` |

Each push has a single bounded `asyncio.Queue` of size `stream_buffer_size`
(default 256). On overflow the **oldest** item is dropped and a warning is
logged — this matches the C++ SDK's behaviour. Both the iterator and the
matching `on_*` callback fire for the same item.

### Push-payload reference

| Push | Field highlights | Typical use |
|------|------------------|-------------|
| `PositionsSnapshot` | `rows: tuple[PositionRow, …]` (`symbol_id, side, size, entry_price, leverage, mark_price, unrealized_pnl, notional, mark_publish_time_sec`), `source` (`INITIAL` / `PERIODIC` / `EVENT`), `server_timestamp` | Hydrate the open-positions table on connect; refresh every ~5s |
| `SystemHealthUpdate` | `total_nodes`, `accepting_orders`, `ready`, `degraded`, `exhausted`, `warming`, `draining`, `waiting` | Display node-cluster status; pause submissions if `accepting_orders is False` |
| `BalanceUpdate` | `shielded_balance_raw` (raw lamports-style integer), `timestamp` | Refresh wallet/equity widget after each fill or settlement |
| `MarginAlert` | `owner`, `symbol_id`, `tier`, `margin_ratio_bps`, `mark_price_bps`, `liquidation_price_bps`, `recovered`, `state_version`, `ts` | Show / clear the margin-tier banner per `(owner, symbol_id)` |
| `FundingRateUpdate` | `symbol_id`, `current_rate`, `predicted_rate`, `next_funding_time`, `timestamp` | Update funding ticker / book metadata |
| `SettlementUpdate` | `batch_id`, `status` (`SettlementBatchStatus`: `SUBMITTED` / `CONFIRMED` / `FAILED`), `tx_signature`, `affected_user_uuids: tuple[str, …]` | Reconcile settled batches, surface Solana tx links |

### Concurrency rule

Only one command (`place_order`, `cancel_order`, `modify_order`) should be in
flight at a time — they share a single acknowledgement slot internally.
`await` each call before issuing the next.

## Core Types

**Module:** `godark.types` (re-exported from `godark`)

All types are frozen `@dataclass` instances. Numeric values are returned as
strings to preserve sequencer-side decimal precision.

### OrderAck

- `order_id: str`
- `success: bool`
- `sequence: str`
- `error_code: str | None`
- `error: str | None`

### OrderUpdate

Lifecycle event for one order. Fields:
`order_id`, `user_uuid`, `symbol_id` (int), `side` (`Side`), `status`
(`OrderStatus`), `update_type` (`OrderUpdateType`), `price`, `quantity`,
`filled_qty`, `remaining_qty`, `cum_fill`, `cancel_reason`
(`CancelReason | None`), `reject_reason` (`str | None`), `correlation_id`,
`timestamp`, `leverage`, `realized_pnl`.

`realized_pnl` and `leverage` are populated on closing/terminal fills when the
sequencer includes them.

### PositionUpdate

Position lifecycle event. Fields:
`user_uuid`, `symbol_id` (int), `side` (`Side`), `update_type`
(`PositionUpdateType`), `size`, `entry_price`, `previous_size`, `fill_price`,
`fill_qty`, `correlation_id`, `timestamp`.

`PositionUpdateType.FUNDING_APPLIED` is delivered for funding accruals — it
appears in this Python SDK in addition to the standard Open / Increase /
Decrease / Close / Snapshot transitions.

### PositionRow / PositionsSnapshot

`PositionRow`: `symbol_id`, `side`, `size`, `entry_price`, `leverage`,
`mark_price`, `unrealized_pnl`, `notional`, `mark_publish_time_sec`.

`PositionsSnapshot`: `user_uuid`, `rows: tuple[PositionRow, …]`,
`server_timestamp`, `source` (`PositionsSnapshotSource`), `correlation_id`.

### Other push payloads

`SystemHealthUpdate`, `BalanceUpdate`, `MarginAlert`, `FundingRateUpdate`,
`SettlementUpdate` — fields listed in the *Push-payload reference* table above.

### UnknownSequencerPush

Fallback delivered to `on_error` when an inner protobuf `oneof` arrives that
this SDK revision does not map. Carries `oneof_field` for diagnostic logging.

## Enums

**Module:** `godark.enums` (re-exported from `godark`)

All enums inherit from `str, Enum`, so values compare/serialize as their string
name (e.g. `Side.SELL == "SELL"`, `str(OrderType.LIMIT) == "OrderType.LIMIT"`,
`OrderType.LIMIT.value == "LIMIT"`).

- `Side`: `BUY`, `SELL`
- `OrderType`: `MARKET`, `LIMIT`, `PEG_TO_MID`, `PEG_TO_BID`, `PEG_TO_ASK`
- `TimeInForce`: `GTC`, `IOC`, `FOK`, `GTD`
- `OrderStatus`: `NEW`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`
- `OrderUpdateType`: `OPEN`, `FILLED`, `PARTIALLY_FILLED`, `CANCELLED`, `REJECTED`, `MODIFIED`, `CANCEL_REJECTED`, `MODIFY_REJECTED`
- `PositionUpdateType`: `SNAPSHOT`, `OPEN`, `INCREASE`, `DECREASE`, `CLOSE`, `FUNDING_APPLIED`
- `CancelReason`: `USER_REQUESTED`, `IOC_REMAINDER`, `FOK_NOT_FILLED`, `EXPIRED`, `SYSTEM`
- `PositionsSnapshotSource`: `UNSPECIFIED`, `INITIAL`, `PERIODIC`, `EVENT`
- `SettlementBatchStatus`: `UNSPECIFIED`, `SUBMITTED`, `CONFIRMED`, `FAILED`

Note: the SDK enum includes additional order types (`PEG_TO_*`) for
compatibility, but this MM distribution supports placing only `MARKET` and
`LIMIT` orders.

## Errors

**Module:** `godark.errors` (re-exported from `godark`)

All SDK exceptions inherit from `godark.GodarkError` (which itself extends the
built-in `Exception`):

```text
GodarkError
├── AuthenticationError   # API key / handshake auth failed
├── SessionError          # Noise XK handshake or rekey failed
├── OrderError            # sequencer rejected the order; carries .error_code
├── ConnectionError       # WebSocket transport / not-connected guard
├── EncryptionError       # AES-GCM encryption / decryption failed
└── TimeoutError          # command stalled waiting for ack
```

`OrderError` carries an optional `.error_code: str | None` with the symbolic
reason (e.g. `"PRICE_DEVIATION_TOO_LARGE"`, `"MARGIN_INSUFFICIENT"`). See
`examples/full_trader_example.py` and `examples/dotenv.py`'s
`print_order_error()` for the catch-and-print pattern.

`godark.ConnectionError` and `godark.TimeoutError` intentionally shadow the
built-in names within this module — import them as `godark.ConnectionError` if
you also need the stdlib variants in the same scope.

### Error-code helpers

| Helper | Purpose |
|--------|---------|
| `make_order_error_from_code(code: int)` | Numeric ack code → richer `OrderError(message, error_code=symbolic)` |
| `make_order_error_from_json(reason=?, code=?)` | JSON acknowledgement variant |
| `find_order_error_code(int)` / `find_order_error_symbolic(str)` | Lookup against `ORDER_ERROR_CODES` table |

## Example files in this distribution

| File | Purpose |
|------|---------|
| `examples/quickstart.py` | Minimal connect, `LIMIT` placement + cancel |
| `examples/full_trader_example.py` | Reference bot flow: callbacks for every push, place / modify / cancel, session summary |
| `examples/dotenv.py` | Stdlib-only `.env` loader and `print_order_error()` helper |

## Installing the SDK

Recipients run `bash scripts/setup_venv.sh` after copying `.env.example` →
`.env`. The script:

1. Creates a `.venv` with the first `python3 >= 3.10` it finds (override with `PYTHON=/path/to/python3.12`).
2. If the bundle includes `wheels/godark-*.whl`, installs that wheel; otherwise installs the vendored `sdk/` directory in editable-equivalent mode (`pip install ./sdk`).
3. Resolves third-party dependencies (`websockets`, `cryptography`, `protobuf`, `httpx`) from PyPI via the wheel/sdist metadata.

Force install from the vendored sources (debugging) with
`PREFER_SDK_SOURCE=1 bash scripts/setup_venv.sh`.

To use `godark` from your own project, install the wheel directly:

```bash
pip install /path/to/wheels/godark-0.1.0-py3-none-any.whl
```

```python
from godark import GodarkClient, OrderType, Side, TimeInForce
```

## SDK layout (`sdk/`)

```text
sdk/
├── pyproject.toml            # Hatch metadata (websockets, cryptography, protobuf, httpx)
├── README.md
├── shared/symbols.json
└── godark/
    ├── __init__.py           # public re-exports
    ├── client.py             # GodarkClient: pushes + Noise XK encrypted trading
    ├── enums.py
    ├── types.py
    ├── errors.py
    ├── order_error_code.py   # symbolic error table + helpers
    └── _generated/…          # committed *_pb2.py protobuf stubs
```

Maintainers refresh `sdk/` from a sibling upstream checkout:

```bash
./scripts/refresh_sdk.sh /path/to/gdx-python-sdk
```
