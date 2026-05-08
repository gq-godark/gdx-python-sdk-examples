#!/usr/bin/env bash
# Create `.venv` and install godark from the MM bundle only — no private godark package index.
#
# Install order:
#   1. wheels/godark-*.whl at repo root (from packaged tarball) → pip install that wheel.
#   2. Else vendored sdk/ → pip install ./sdk
#
# Dependencies (cryptography, websockets, …) resolve from PyPI via wheel/sdist metadata — same
# idea as shipping libgodark.a in the C++ MM bundle: the SDK artifact is local; only generic
# third-party deps download from the internet.
#
# Requires Python ≥ 3.10 with venv support — install python3-venv on Debian/Ubuntu if needed.
# Override: PYTHON=/path/to/python3.12 ; PREFER_SDK_SOURCE=1 forces pip install ./sdk.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
AUTO_ACTIVATE="${AUTO_ACTIVATE:-1}"
SDK_DIR="${ROOT_DIR}/sdk"
WHEEL_DIR="${ROOT_DIR}/wheels"
PREFER_SDK_SOURCE="${PREFER_SDK_SOURCE:-0}"

python_ge_310() {
  local exe="$1"
  "$exe" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

pick_python() {
  local cand=""
  if [[ -n "${PYTHON:-}" ]]; then
    cand="${PYTHON}"
    if python_ge_310 "${cand}"; then
      printf '%s' "${cand}"
      return 0
    fi
    echo "error: PYTHON=${PYTHON} is not usable or is < 3.10" >&2
    exit 1
  fi
  for cand in python3 python; do
    if command -v "${cand}" >/dev/null 2>&1 && python_ge_310 "${cand}"; then
      printf '%s' "${cand}"
      return 0
    fi
  done
  echo "error: need Python >= 3.10 with venv (install python3 python3-venv python3-pip; or set PYTHON=/path/to/python3)" >&2
  exit 1
}

pick_wheel() {
  [[ -d "${WHEEL_DIR}" ]] || return 0
  shopt -s nullglob
  local arr=( "${WHEEL_DIR}"/godark-*.whl )
  shopt -u nullglob
  [[ ${#arr[@]} -eq 0 ]] && return 0
  ls -t "${arr[@]}" | head -1
}

if [[ ! -d "${SDK_DIR}/godark" || ! -f "${SDK_DIR}/pyproject.toml" ]]; then
  echo "error: vendored sdk missing under ${SDK_DIR}/" >&2
  exit 1
fi

PY_EXE="$(pick_python)"

if ! "${PY_EXE}" -m venv -h >/dev/null 2>&1; then
  echo "error: '${PY_EXE}' has no venv module — on Debian/Ubuntu install: sudo apt-get install python3-venv" >&2
  exit 1
fi

echo "Creating virtualenv at ${VENV_DIR} using ${PY_EXE}"
rm -rf "${VENV_DIR}"
"${PY_EXE}" -m venv "${VENV_DIR}"

"${VENV_DIR}/bin/pip" install --upgrade pip

WHEEL_PATH=""
if [[ "${PREFER_SDK_SOURCE}" != "1" ]]; then
  WHEEL_PATH="$(pick_wheel)"
fi

if [[ "${PREFER_SDK_SOURCE}" == "1" ]]; then
  echo "PREFER_SDK_SOURCE=1 — installing from vendored sdk/"
  "${VENV_DIR}/bin/pip" install "${SDK_DIR}"
elif [[ -n "${WHEEL_PATH}" ]]; then
  echo "Installing godark from packaged wheel: ${WHEEL_PATH}"
  "${VENV_DIR}/bin/pip" install "${WHEEL_PATH}"
else
  echo "Installing godark from vendored sdk/ (no wheels/godark-*.whl found)"
  "${VENV_DIR}/bin/pip" install "${SDK_DIR}"
fi

echo
if [[ -n "${WHEEL_PATH}" && "${PREFER_SDK_SOURCE}" != "1" ]]; then
  echo "Done. Installed wheel: ${WHEEL_PATH}"
else
  echo "Done. Installed from: ${SDK_DIR}"
fi
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
  echo "  source .venv/bin/activate"
fi
