#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -x ".venv-pypy/bin/python" ]]; then
  echo "Missing .venv-pypy. Run: bash scripts/setup_pypy.sh"
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if [[ -z "${GODARK_API_KEY_ID:-}" || -z "${GODARK_API_SECRET:-}" ]]; then
  echo "Missing credentials."
  echo "Set GODARK_API_KEY_ID and GODARK_API_SECRET in .env (or export them in terminal)."
  exit 1
fi

export GODARK_EDGE_URL="${GODARK_EDGE_URL:-wss://api.godark-dex.com}"
export GDX_REST_URL="${GDX_REST_URL:-https://api.godark-dex.com/api/v1}"

PY=".venv-pypy/bin/python"

echo "[1/6] market_data_example (10s)"
timeout 12s "${PY}" examples/market_data_example.py --symbol ETH-USDT-PERP --duration-seconds 10 || true

echo "[2/6] e2e_trading_smoke --auth-only"
"${PY}" examples/e2e_trading_smoke.py --auth-only

echo "[3/6] e2e_trading_smoke full flow"
"${PY}" examples/e2e_trading_smoke.py

echo "[4/6] quickstart"
"${PY}" examples/quickstart.py

echo "[5/6] full_trader_rest"
"${PY}" examples/full_trader_rest.py

echo "[6/6] full_trader_example"
"${PY}" examples/full_trader_example.py

echo
echo "All examples completed."
