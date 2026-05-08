#!/usr/bin/env bash
# Create .venv-pypy (or CPython 3.10+) and pip-install the vendored sdk/ tree.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv-pypy"
AUTO_ACTIVATE="${AUTO_ACTIVATE:-1}"
FORCE_PYPY="${FORCE_PYPY:-0}"
SDK_DIR="${ROOT_DIR}/sdk"

run_as_root() {
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

python_ge_310() {
  local exe="$1"
  "$exe" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

if [[ ! -d "${SDK_DIR}/godark" || ! -f "${SDK_DIR}/pyproject.toml" ]]; then
  echo "error: vendored sdk missing under ${SDK_DIR}/" >&2
  exit 1
fi

if ! command -v pypy3 >/dev/null 2>&1; then
  echo "pypy3 not found. Installing PyPy runtime..."
  run_as_root apt-get update
  run_as_root apt-get install -y pypy3 pypy3-venv
fi

if ! pypy3 -m venv -h >/dev/null 2>&1; then
  echo "PyPy venv module unavailable. Installing pypy3-venv..."
  run_as_root apt-get update
  run_as_root apt-get install -y pypy3-venv
fi

PY_EXE="pypy3"
if ! python_ge_310 "${PY_EXE}"; then
  if [[ "${FORCE_PYPY}" == "1" ]]; then
    echo "PyPy version is < 3.10, but FORCE_PYPY=1 is set. Aborting."
    echo "Installed PyPy: $(pypy3 -V 2>&1 | tr '\n' ' ')"
    exit 1
  fi
  if command -v python3 >/dev/null 2>&1 && python_ge_310 python3; then
    echo "Warning: installed PyPy is < 3.10; falling back to python3 for venv."
    echo "Installed PyPy: $(pypy3 -V 2>&1 | tr '\n' ' ')"
    PY_EXE="python3"
  else
    echo "No compatible Python runtime found (need >= 3.10)."
    exit 1
  fi
fi

echo "Creating virtualenv at ${VENV_DIR} using ${PY_EXE}"
rm -rf "${VENV_DIR}"
"${PY_EXE}" -m venv "${VENV_DIR}"

echo "Installing godark from vendored sdk/"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install "${SDK_DIR}"

echo
echo "Done. Installed from: ${SDK_DIR}"
if [[ "${AUTO_ACTIVATE}" == "1" && "${BASH_SOURCE[0]}" != "$0" ]]; then
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
  echo "Activated: ${VENV_DIR}"
elif [[ "${AUTO_ACTIVATE}" == "1" && -t 1 ]]; then
  echo "Launching interactive shell with venv activated..."
  echo "Use 'exit' to return to your previous shell."
  exec bash --rcfile <(printf 'source "%s/bin/activate"\n' "${VENV_DIR}") -i
else
  echo "Activate with:"
  echo "  source .venv-pypy/bin/activate"
fi
