# GoDark Python Examples (Darkpool MM distribution)

This repository is a market-maker-facing distribution for GoDark’s Python SDK.
It includes:

- a vendored **`godark` wheel** (built when you run `scripts/package.sh`) plus full **`sdk/`** sources — **no private godark package registry is required**, same idea as shipping **`libgodark.a`** in the C++ MM bundle or vendoring crates in Rust examples
- minimal darkpool trading examples (**market** and **limit** orders only in the samples)
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
- `GDX_NOISE_STATIC_PUBLIC_KEY` — required for encrypted WebSocket trading (64-hex sequencer static key). Aliases: `GDX_NOISE_STATIC_PUBKEY`, `GODARK_NOISE_STATIC_PUBLIC_KEY`.

Optional:

- `GODARK_EDGE_URL` — local testing only; if unset, examples use `wss://api.godark-dex.com`.

Some local edges require a user UUID from auth; set `GODARK_USER_UUID` when needed.

## Install

### From a packaged tarball (recommended for MMs)

Unpack the archive you received. It contains `wheels/godark-*.whl`, vendored `sdk/`, `examples/`, and `scripts/setup_venv.sh`.

```bash
bash scripts/setup_venv.sh
source .venv/bin/activate
cd examples && python quickstart.py
python full_trader_example.py
```

`setup_venv.sh` **prefers installing the packaged wheel** under `wheels/` (immutable SDK snapshot). Dependencies such as `cryptography` are pulled from PyPI using the wheel’s metadata.

To force install from the vendored source tree instead (debugging):

```bash
PREFER_SDK_SOURCE=1 bash scripts/setup_venv.sh
```

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

## Examples

| Script | Purpose |
|--------|---------|
| `examples/quickstart.py` | Minimal connect → LIMIT sell far from touch → cancel |
| `examples/full_trader_example.py` | Callbacks for pushes, place / modify / cancel, session summary |

Order-type support in this MM distribution is limited to **`MARKET`** and **`LIMIT`**.

## Packaging for market makers

Create a clean distributable archive:

```bash
bash scripts/package.sh              # godark-python-examples.tar.gz
bash scripts/package.sh my-release   # custom archive name stem
```

The tarball includes:

- `sdk/` — vendored package sources (including generated protobuf under `godark/_generated/`)
- `wheels/` — `godark-*.whl` built from `sdk/` (`pip wheel --no-deps`; runtime deps install via pip when the wheel is installed)
- `examples/` — MM example scripts
- `scripts/setup_venv.sh` — bootstrap script for recipients
- `README.md`, `SDK_REFERENCE.md`, `.env.example`

Internal-only paths (`scripts/package.sh`, `scripts/refresh_sdk.sh`, `.git/`, local `.env`, virtualenvs, build artifacts) are **not** included.

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
