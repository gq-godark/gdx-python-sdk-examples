#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv-pypy"
GODARK_PYPI_SPEC="${GODARK_PYPI_SPEC:-godark}"
GODARK_GIT_SPEC="${GODARK_GIT_SPEC:-}"
LOCAL_SDK_DIR="${ROOT_DIR}/../gdx-python-sdk"
AUTO_ACTIVATE="${AUTO_ACTIVATE:-1}"
FORCE_PYPY="${FORCE_PYPY:-0}"

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

echo "Installing ${GODARK_PYPI_SPEC} from package index"
"${VENV_DIR}/bin/pip" install --upgrade pip
if "${VENV_DIR}/bin/pip" install "${GODARK_PYPI_SPEC}"; then
  INSTALL_SOURCE="package index (${GODARK_PYPI_SPEC})"
elif [[ -n "${GODARK_GIT_SPEC}" ]]; then
  echo "Package install failed; trying git source: ${GODARK_GIT_SPEC}"
  "${VENV_DIR}/bin/pip" install "${GODARK_GIT_SPEC}"
  INSTALL_SOURCE="git (${GODARK_GIT_SPEC})"
elif [[ -d "${LOCAL_SDK_DIR}" ]]; then
  echo "Package install failed; falling back to local SDK: ${LOCAL_SDK_DIR}"
  "${VENV_DIR}/bin/pip" install -e "${LOCAL_SDK_DIR}"
  INSTALL_SOURCE="local editable (${LOCAL_SDK_DIR})"
else
  echo "Failed to install ${GODARK_PYPI_SPEC} from package index."
  echo "No GODARK_GIT_SPEC provided and local SDK not found at ${LOCAL_SDK_DIR}."
  echo "Set either:"
  echo "  GODARK_GIT_SPEC='git+https://<repo>.git'"
  echo "or run with sibling SDK checkout present."
  exit 1
fi

echo
echo "Done."
echo "Installed from: ${INSTALL_SOURCE}"
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
echo
echo "Tip: pin a version with:"
echo "  GODARK_PYPI_SPEC='godark==0.1.0' bash scripts/setup_pypy.sh"
echo "Tip: auto-activate in current shell:"
echo "  source scripts/setup_pypy.sh"
