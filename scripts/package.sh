#!/usr/bin/env bash
# MM bundle packager — wheels-only zip distribution.
#
# Mirrors the layout of the reference bundle (gdx-python-sdk-fixed.zip):
#
#   <DIST_NAME>/
#   ├── .env.example
#   ├── README.md             (from bundle/README.md — recipient-facing)
#   ├── SDK_REFERENCE.md      (from bundle/SDK_REFERENCE.md — recipient-facing)
#   ├── examples/
#   │   ├── dotenv.py
#   │   ├── full_trader_example.py
#   │   └── quickstart.py
#   └── wheels/
#       └── godark-*.whl       (built from sdk/ via `pip wheel --no-deps`)
#
# Internal-only paths (sdk/ source tree, scripts/, repo-root README.md /
# SDK_REFERENCE.md, .git/, local .env, virtualenvs, build artifacts) are
# intentionally NOT shipped.
#
# Usage:
#   bash scripts/package.sh                              # default: gdx-python-sdk-examples-feat-v2.zip
#   bash scripts/package.sh my-release                   # custom dist-name stem -> my-release.zip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_NAME="${1:-gdx-python-sdk-examples-feat-v2}"

cd "$REPO_ROOT"

# ---- pre-flight ------------------------------------------------------------
if [[ ! -f "${REPO_ROOT}/sdk/pyproject.toml" ]]; then
  echo "error: sdk/pyproject.toml missing — cannot build wheel" >&2
  exit 1
fi
for required in bundle/README.md bundle/SDK_REFERENCE.md .env.example examples/quickstart.py examples/full_trader_example.py examples/dotenv.py; do
  if [[ ! -f "${REPO_ROOT}/${required}" ]]; then
    echo "error: required source file missing: ${required}" >&2
    exit 1
  fi
done
if ! command -v zip >/dev/null 2>&1; then
  echo "error: 'zip' not found in PATH (apt-get install zip)" >&2
  exit 1
fi

# ---- build wheel ----------------------------------------------------------
echo "Building wheel from sdk/ ..."
rm -rf "${REPO_ROOT}/sdk/dist-wheels"
mkdir -p "${REPO_ROOT}/sdk/dist-wheels"
(
  cd "${REPO_ROOT}/sdk"
  python3 -m pip wheel . --no-deps -w dist-wheels/
)

# ---- stage ----------------------------------------------------------------
STAGING_DIR="$(mktemp -d)"
DEST="$STAGING_DIR/$DIST_NAME"
mkdir -p "$DEST/examples" "$DEST/wheels"

echo "Staging wheels-only distribution at $DEST ..."
cp "${REPO_ROOT}/sdk/dist-wheels"/godark-*.whl "$DEST/wheels/"
cp "${REPO_ROOT}/examples/quickstart.py" \
   "${REPO_ROOT}/examples/full_trader_example.py" \
   "${REPO_ROOT}/examples/dotenv.py" \
   "$DEST/examples/"
cp "${REPO_ROOT}/.env.example" "$DEST/"
cp "${REPO_ROOT}/bundle/README.md" "$DEST/README.md"
cp "${REPO_ROOT}/bundle/SDK_REFERENCE.md" "$DEST/SDK_REFERENCE.md"

# ---- zip ------------------------------------------------------------------
ARCHIVE="$REPO_ROOT/${DIST_NAME}.zip"
rm -f "$ARCHIVE"
( cd "$STAGING_DIR" && zip -qr "$ARCHIVE" "$DIST_NAME" )
rm -rf "$STAGING_DIR"

# ---- post-flight assertions ----------------------------------------------
echo
echo "Package created: $ARCHIVE"
LISTING="$(unzip -l "$ARCHIVE")"
echo "$LISTING"

if echo "$LISTING" | grep -E "${DIST_NAME}/(sdk|scripts)/" >/dev/null; then
  echo "error: bundle contains sdk/ or scripts/ — wheels-only contract violated" >&2
  exit 1
fi
for required in \
  "${DIST_NAME}/wheels/godark-.*\\.whl" \
  "${DIST_NAME}/examples/quickstart\\.py" \
  "${DIST_NAME}/examples/full_trader_example\\.py" \
  "${DIST_NAME}/examples/dotenv\\.py" \
  "${DIST_NAME}/README\\.md" \
  "${DIST_NAME}/SDK_REFERENCE\\.md" \
  "${DIST_NAME}/\\.env\\.example"; do
  if ! echo "$LISTING" | grep -E "${required}" >/dev/null; then
    echo "error: bundle missing required entry: ${required}" >&2
    exit 1
  fi
done

echo
echo "wheels-only assertion: PASSED"
