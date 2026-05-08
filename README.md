# GoDark Python Examples (Darkpool MM distribution)

Self-contained trading examples with the `godark` SDK vendored under `sdk/`.

- Two MM-facing examples (`quickstart`, `full_trader_example`).
- Install from `sdk/` with `pip` — no private package index required.
- Pre-generated protobuf stubs live in `sdk/godark/_generated/`; **no `protoc`**
  needed.
- Minimal `.env` workflow at the repo root.

## Two ways to use

### A — tarball (no editable checkout)

Unpack the archive you received, copy `.env.example` → `.env`, then:

```bash
bash scripts/setup_pypy.sh
source .venv-pypy/bin/activate
cd examples && python quickstart.py
python full_trader_example.py
```

(Optional) Install the bundled wheel instead of `pip install sdk/`:

```bash
pip install wheels/godark-*.whl
```

### B — Clone this repository

```bash
bash scripts/setup_pypy.sh
source .venv-pypy/bin/activate
cd examples && python quickstart.py
```

## Platforms

| Item | Requirement |
|------|--------------|
| Python | ≥ 3.10 (PyPy 3.10+ or CPython) |
| OS | Linux x86_64 recommended for parity with packaged tarballs |
| Crypto | Uses `cryptography` (OpenSSL 3 typical on Ubuntu 22.04/24.04) |

## Testnet onboarding

1. Open `https://app.godark-dex.com`
2. Create an account (email sign-up).
3. Fund via `https://faucet.godark-dex.com`
4. Settings → API Key Management → create a key pair.

## Configure credentials

```bash
cp .env.example .env
```

Set:

- `GODARK_API_KEY_ID`
- `GODARK_API_SECRET`
- `GODARK_EDGE_URL` (optional; defaults to `wss://api.godark-dex.com`)

Optional local edge override example:

```
GODARK_EDGE_URL=ws://127.0.0.1:4000
```

The client normalizes trailing `/ws`, `/ws/v1`, etc. (`ws://host:4000` and
`ws://host:4000/ws/v1` both resolve to the same trading socket).

Some local edge setups require user UUID propagation; set:

```
GODARK_USER_UUID=<uuid-from-auth-result>
```

## Examples

| Script | Purpose |
|--------|---------|
| `examples/quickstart.py` | Connect → LIMIT sell far from touch → cancel |
| `examples/full_trader_example.py` | All push callbacks + place / modify / cancel + summary |

This distribution exposes **LIMIT** and **MARKET** placement only via the MM
samples (other pegged types remain in the vendored enums for completeness).

## Packaging for market makers

```bash
bash scripts/package.sh          # godark-python-examples-linux-x86_64.tar.gz
bash scripts/package.sh my-dist  # custom archive name stem
```

## Layout

```
./
├── README.md               # This file
├── SDK_REFERENCE.md        # Detailed API cheat sheet (MM-oriented)
├── .env.example
├── examples/
│   ├── dotenv.py           # `load_dotenv` + `print_order_error`
│   ├── quickstart.py
│   └── full_trader_example.py
├── scripts/
│   ├── setup_pypy.sh       # Bootstrap venv + pip install ./sdk
│   ├── refresh_sdk.sh      # Re-vendor sdk from a sibling gdx-python-sdk
│   └── package.sh          # Tarball exporter
└── sdk/
    ├── pyproject.toml      # Hatchling wheel/manifest for `godark`
    ├── README.md
    ├── shared/symbols.json
    └── godark/             # SDK source (+ committed `_generated` protos)
```

## Refreshing `sdk/` (internal)

From a sibling development checkout:

```bash
./scripts/refresh_sdk.sh /path/to/gdx-python-sdk
```

Then remove `.venv-pypy` (or rerun `scripts/setup_pypy.sh`) so the refreshed
sources are pip-installed clean.
