# GoDark Python SDK

This package provides the GoDark Python SDK and minimal examples for encrypted
darkpool trading.

Supported order types in this distribution: `MARKET`, `LIMIT`.

## Package contents

- `wheels/` — `godark-*.whl`
- `examples/` — `quickstart.py`, `full_trader_example.py`
- `SDK_REFERENCE.md` — API reference
- `.env.example` — environment template

## 1) Prerequisites

- Linux x86_64
- Python >= 3.10 (CPython recommended), with `venv` support
- `pip` with network access to PyPI for runtime deps

Install dependencies:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv
```

Use `python3.12` (or your preferred interpreter) explicitly if multiple Python versions are installed.

## 2) Create testnet credentials

1. Open frontend: `https://app.godark-dex.com`
2. Create an account using email.
3. Fund the account using faucet: `https://faucet.godark-dex.com`
4. Go to **Settings -> API Key Management** and create an API key.

## 3) Configure environment

Copy `.env.example` to `.env` and set:

- `GODARK_API_KEY_ID`
- `GODARK_API_SECRET`
- `GODARK_PASSPHRASE`

Public testnet needs only the three credential keys above for hosted testnet; localnet/devnet also require `GDX_HPKE_STATIC_PUBLIC_KEY`.

Optional:

- `GODARK_EDGE_URL` — override the edge URL.
- `GDX_HPKE_STATIC_PUBLIC_KEY` — override the sequencer HPKE pin (**not required for testnet**). Aliases: `GDX_HPKE_STATIC_PUBKEY`, `GODARK_HPKE_STATIC_PUBLIC_KEY`.

```bash
cp .env.example .env
```

## 4) Install the SDK

Create a virtualenv and install the wheel:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install wheels/godark-*.whl
```

Prices, sizes, quote notional, min fill, trigger, take-profit, and stop-loss are strings. `int`, `float`, and `bool` raise `TypeError`.

WebSocket login is the REST `client_credentials` access token, not `key:secret:passphrase`. `connect()` obtains that token.

Subscribe on `/ws/v1` to `orders`, `positions`, `volume`, `open_interest`, and `funding_rate`. Trades and L2 are not on this socket.

`client_order_id` is registered only after a successful WebSocket place. REST place does not register it.

`slippage_bps` is only for `MARKET` and `STOP_MARKET`. `PEG` is not post-only.

`GodarkRestClient(..., rest_base_url=...)` — `place_order` is keyword-only after `side`.

```python
import os
from godark import GodarkClient, GodarkRestClient, OrderType, Side, TimeInForce

rest = GodarkRestClient(
    api_key_id=os.environ["GODARK_API_KEY_ID"],
    api_secret=os.environ["GODARK_API_SECRET"],
    passphrase=os.environ["GODARK_PASSPHRASE"],
    rest_base_url=os.environ.get("GODARK_REST_URL", "https://api.godark-dex.com"),
)
# await rest.connect(); positions = await rest.get_positions()

async def trade():
    async with GodarkClient(
        api_key_id=os.environ["GODARK_API_KEY_ID"],
        api_secret=os.environ["GODARK_API_SECRET"],
        passphrase=os.environ["GODARK_PASSPHRASE"],
        base_url=os.environ.get("GODARK_EDGE_URL", "wss://api.godark-dex.com"),
    ) as client:
        await client.subscribe(["orders", "positions"])
        ack = await client.place_order(
            "BTC-USDC-PERP", Side.SELL, OrderType.LIMIT, "0.01",
            price="999999.0", time_in_force=TimeInForce.GTC,
        )
        await client.cancel_order(ack.order_id, "BTC-USDC-PERP")
```

## 5) Run quickstart

```bash
python examples/quickstart.py
```

## pip integration (your own bot)

Install the wheel into your own project's virtualenv:

```bash
pip install path/to/godark-python-sdk/wheels/godark-*.whl
```

Then in `my_bot.py`:

```python
import asyncio
import os

from godark import GodarkClient, OrderType, Side, TimeInForce

async def main():
    async with GodarkClient(
        api_key_id=os.environ["GODARK_API_KEY_ID"],
        api_secret=os.environ["GODARK_API_SECRET"],
        passphrase=os.environ["GODARK_PASSPHRASE"],
        base_url=os.environ.get("GODARK_EDGE_URL", "wss://api.godark-dex.com"),
    ) as client:
        ack = await client.place_order(
            "BTC-USDC-PERP",
            Side.SELL,
            OrderType.LIMIT,
            "0.01",
            price="999999.0",
            time_in_force=TimeInForce.GTC,
        )
        await client.cancel_order(str(ack.order_id), "BTC-USDC-PERP")

asyncio.run(main())
```

See `SDK_REFERENCE.md` for full client API usage.
