# Godark Python SDK examples

Sample programs that consume the published `godark` package using a **PyPy**
virtual environment. This mirrors the intent of `gdx-cpp-sdk-examples`: examples
repo is independent from SDK source checkout.

Credentials are read from **shell exports** (terminal environment variables),
not from a `.env` file.

## Prerequisites

- `pypy3` (recommended: PyPy 3.10+)
- Access to a package index that serves `godark`
- API credentials for private trading examples:
  - `GODARK_API_KEY_ID`
  - `GODARK_API_SECRET`

## Setup (PyPy)

From this repository root:

```bash
bash scripts/setup_pypy.sh
source .venv-pypy/bin/activate
```

This creates `.venv-pypy` and installs `godark` from your configured package
index.

If the package is not available on the index, setup falls back in this order:
1. `GODARK_GIT_SPEC` (if provided)
2. local sibling checkout at `../gdx-python-sdk` (editable install)

Runtime behavior:
- Prefers `pypy3` when its Python version is `>=3.10`
- Falls back to `python3` when installed PyPy is too old for `godark`
- Set `FORCE_PYPY=1` to fail instead of falling back

If `pypy3` is missing, the script bootstraps it via `apt-get` (`sudo` if available).
To auto-activate in the current shell, run:

```bash
source scripts/setup_pypy.sh
```

Running `bash scripts/setup_pypy.sh` in an interactive terminal now opens a new
activated shell automatically (exit that shell to return).

Optional version pin:

```bash
GODARK_PYPI_SPEC="godark==0.1.0" bash scripts/setup_pypy.sh
```

Optional git source:

```bash
GODARK_GIT_SPEC="git+https://github.com/gq-godark/gdx-python-sdk.git" bash scripts/setup_pypy.sh
```

## Examples

| Script | Path | What it does |
|--------|------|--------------|
| `quickstart` | `examples/quickstart.py` | Minimal connect -> limit sell -> cancel. |
| `e2e_trading_smoke` | `examples/e2e_trading_smoke.py` | CI-friendly auth-only or full place+cancel smoke check. |
| `market_data_example` | `examples/market_data_example.py` | Public gomarket orderbook + trades (no keys). |
| `full_trader_example` | `examples/full_trader_example.py` | Expanded demo: callbacks, stream drain, place/modify/cancel. |
| `full_trader_rest` | `examples/full_trader_rest.py` | REST client flow: encrypted place + fetch + cancel by id. |

## Run

```bash
source .venv-pypy/bin/activate

export GODARK_API_KEY_ID="gdk_your_key_id"
export GODARK_API_SECRET="your_secret"
export GODARK_EDGE_URL="wss://api.godark-dex.com"
export GDX_REST_URL="https://api.godark-dex.com/api/v1"

# 1) Auth + ECDH only
python examples/e2e_trading_smoke.py --auth-only

# 2) Full WS trading smoke
python examples/e2e_trading_smoke.py

# 3) Market data only
python examples/market_data_example.py --symbol ETH-USDT-PERP
```

## Run all examples

Set credentials in terminal, then run the helper script:

```bash
source .venv-pypy/bin/activate
export GODARK_API_KEY_ID="gdk_your_key_id"
export GODARK_API_SECRET="your_secret"
export GODARK_EDGE_URL="wss://api.godark-dex.com"
export GDX_REST_URL="https://api.godark-dex.com/api/v1"
bash scripts/run_all_examples.sh
```

The script runs:
1. `market_data_example.py` (short public smoke)
2. `e2e_trading_smoke.py --auth-only`
3. `e2e_trading_smoke.py` (place/cancel)
4. `quickstart.py`
5. `full_trader_rest.py`
6. `full_trader_example.py`

## Environment quick reference

- **Trading WS examples**
  - `GODARK_API_KEY_ID` / `GDX_API_KEY_ID`
  - `GODARK_API_SECRET` / `GDX_API_SECRET`
  - `GODARK_EDGE_URL` / `GDX_EDGE_URL` (default: `wss://api.godark-dex.com`)
- **REST example**
  - `GDX_REST_URL` / `GODARK_REST_BASE` (default: `https://api.godark-dex.com/api/v1`)
- **TLS (optional)**
  - `GODARK_TLS_SKIP_VERIFY=1` / `GDX_TLS_SKIP_VERIFY=1`

## Layout

| Path | Purpose |
|------|---------|
| `scripts/setup_pypy.sh` | Creates PyPy venv and installs `godark` package |
| `examples/common.py` | Shared env/config helpers |
| `examples/*.py` | Runnable examples |