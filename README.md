# GoDark Python Examples (Darkpool MM distribution)

This repository is a market-maker-facing distribution for GoDark’s Python SDK.
It includes:

- a vendored **`godark` wheel** (built when you run `scripts/package.sh`) plus full **`sdk/`** sources — **no private godark package registry is required**, same idea as shipping **`libgodark.a`** in the C++ MM bundle or vendoring crates in Rust examples
- minimal darkpool trading examples (post-only **limit** orders priced from the live mark)
- a simple **`.env`** workflow (no shell `export` required)

Third-party libraries (`cryptography`, `websockets`, …) still install from **PyPI** via normal `pip` dependency resolution when you install the wheel or `sdk/` — only the **`godark`** package itself comes entirely from this repo.

## Prerequisites

| Item | Requirement |
|------|-------------|
| Python | ≥ 3.10 (**CPython** recommended), with **`venv`** support |
| OS | Linux x86_64 recommended (matches published tarballs) |

Example on Debian/Ubuntu — install the interpreter and venv once (compare: C++ README lists Boost/OpenSSL for building):

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv
```

Use `PYTHON=/path/to/python3.12` if multiple Python versions are installed.

## Testnet onboarding

Before running the examples, complete this setup flow:

1. Open the testnet frontend: `https://app.godark-dex.com`
2. Create an account using email sign-up.
3. Fund your testnet account using the faucet: `https://faucet.godark-dex.com`
4. In the frontend, go to **Settings → API Key Management** and click **Create API Key**.
5. Use the generated key ID and secret for your local `.env`.

## Configure credentials

Copy `.env.example` to `.env` and fill in your API credentials:

```bash
cp .env.example .env
```

Required keys:

- `GODARK_API_KEY_ID`
- `GODARK_API_SECRET`
- `GODARK_PASSPHRASE` — required for API key-pair auth.

Optional:

- `GODARK_EDGE_URL` — override the edge URL (default: public testnet `wss://api.godark-dex.com` via the SDK Testnet environment preset).
- `GDX_HPKE_STATIC_PUBLIC_KEY` — sequencer HPKE static public key (64 hex). Required for **localnet/devnet** encrypted trading Aliases: `GDX_HPKE_STATIC_PUBKEY`, `GODARK_HPKE_STATIC_PUBLIC_KEY`, `VITE_GDX_HPKE_STATIC_PUBKEY`.

Some local edges require a base58 32-byte account fallback; set
`GODARK_ACCOUNT` when auth does not return `account`.

## Localnet (`gdx up`)

Against a local stack, set in `.env`:

```bash
GODARK_EDGE_URL=ws://127.0.0.1:13300
GODARK_API_KEY=test-key-1
GDX_HPKE_STATIC_PUBLIC_KEY=1d61f116451fdfda1aa4aaf50b7200c3b362d0445bfa2d7ef1f80b3b8881a533
```

Fund the default user: `gdx fund 00000000-0000-4000-8000-000000000001`. Copy `VITE_GDX_HPKE_STATIC_PUBKEY` from `gdx-web/.env.localnet` if your pin differs.

## Install

### From a packaged zip (recommended for MMs)

Unpack the archive you received. It contains `wheels/godark-*.whl`,
`examples/`, `README.md`, `SDK_REFERENCE.md`, and `.env.example`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install wheels/godark-*.whl
cd examples && python quickstart.py
python full_trader_example.py
python rest_client_example.py
```

Dependencies such as `cryptography` are pulled from PyPI using the wheel’s
metadata.

### From a git clone (development)

There is usually **no** pre-built wheel at the repo root — install comes from **`sdk/`**:

```bash
bash scripts/setup_venv.sh
source .venv/bin/activate
cd examples && python quickstart.py
```

To produce a wheel locally (same as release packaging):

```bash
bash scripts/package.sh
# optional: copy sdk/dist-wheels/*.whl into ./wheels/ and rerun setup_venv.sh to test wheel install
```

## Follow the current SDK

Prices, sizes, quote notional, min fill, trigger, take-profit, and stop-loss are **strings**. `int`, `float`, and `bool` raise `TypeError`.

WebSocket `op: login` uses the REST `client_credentials` **access token**. The socket does not take `key:secret:passphrase`. `GodarkClient.connect()` calls `POST /auth/token` and logs in with `access_token`.

`/ws/v1` channels are `orders`, `positions`, `volume`, `open_interest`, and `funding_rate`. Trades and L2 are not on this socket.

`client_order_id` is registered only after a **successful WebSocket** place. A REST place does not register it. Do not treat a process-local map as the lookup.

`slippage_bps` is only for `MARKET` and `STOP_MARKET`. A `PEG` order is not post-only.

`GodarkRestClient` takes `rest_base_url=`. On that client, `place_order` is keyword-only after `side` (`quantity=`, `type=` or `order_type=`, `price=`).

```python
import os
from godark import GodarkClient, GodarkRestClient, OrderType, Side, TimeInForce

rest = GodarkRestClient(
    api_key_id=os.environ["GODARK_API_KEY_ID"],
    api_secret=os.environ["GODARK_API_SECRET"],
    passphrase=os.environ["GODARK_PASSPHRASE"],
    rest_base_url=os.environ.get("GODARK_REST_URL", "https://api.godark-dex.com"),
)
await rest.connect()  # client_credentials → access token
positions = await rest.get_positions()

async with GodarkClient(
    api_key_id=os.environ["GODARK_API_KEY_ID"],
    api_secret=os.environ["GODARK_API_SECRET"],
    passphrase=os.environ["GODARK_PASSPHRASE"],
    base_url=os.environ.get("GODARK_EDGE_URL", "wss://api.godark-dex.com"),
) as client:
    await client.subscribe(["orders", "positions"])
    ack = await client.place_order(
        "BTC-USDC-PERP",
        Side.SELL,
        OrderType.LIMIT,
        "0.01",
        price="999999.0",
        time_in_force=TimeInForce.GTC,
    )
    await client.cancel_order(ack.order_id, "BTC-USDC-PERP")
```

## Examples

| Script | Purpose |
|--------|---------|
| `examples/quickstart.py` | Minimal connect → `subscribe(["orders"])` → LIMIT sell (string price) → cancel |
| `examples/full_trader_example.py` | Callbacks, string place / modify / cancel, mass-quote, session summary |
| `examples/rest_client_example.py` | REST `client_credentials` auth, account reads, positions |
| `examples/full_trader_rest.py` | REST snapshots and keyword `place_order` / modify / cancel |

The trading samples place post-only **`LIMIT`** orders only. They read a live mark (or exit without placing) and cancel only the order that process just placed.

## Packaging for market makers

Create a clean wheels-only distributable archive:

```bash
bash scripts/package.sh              # godark-python-sdk.zip
bash scripts/package.sh my-release   # custom archive name stem
```

The zip includes:

- `wheels/` — `godark-*.whl` built from `sdk/` (`pip wheel --no-deps`; runtime deps install via pip when the wheel is installed)
- `examples/` — MM example scripts
- `README.md`, `SDK_REFERENCE.md`, `.env.example`

Internal-only paths (`sdk/`, `scripts/`, `.git/`, local `.env`, virtualenvs,
build artifacts) are **not** included.

## Layout

| Path | Purpose |
|------|---------|
| `sdk/` | Vendored `godark` package (`pyproject.toml`, `godark/`, `shared/symbols.json`) |
| `wheels/` | Present in **published tarballs** — packaged wheels for `pip install` |
| `examples/` | Runnable MM scripts (`dotenv.py` helpers live beside them) |
| `.env.example` | Credential template copied to `.env` |
| `SDK_REFERENCE.md` | API-oriented reference for integration |
| `scripts/setup_venv.sh` | Create `.venv` and install wheel or `sdk/` |
| `scripts/package.sh` | Build wheel + tarball (maintainers / CI) |
| `scripts/refresh_sdk.sh` | Copy `sdk/` from a sibling `gdx-python-sdk` checkout (maintainers only; not shipped) |

## Refreshing `sdk/` (internal)

From a sibling development checkout of the upstream SDK:

```bash
./scripts/refresh_sdk.sh /path/to/gdx-python-sdk
```

Then remove `.venv` or rerun `scripts/setup_venv.sh` so the refreshed sources are installed cleanly.
