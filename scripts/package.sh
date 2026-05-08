#!/usr/bin/env bash
# MM tarball packager — mirrors the layout of other godark-*-examples bundles:
# vendored sdk/, built wheel for pip installs, examples, docs, credential template.
# Internal-only scripts (this file, refresh_sdk.sh) are not shipped.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_NAME="${1:-godark-python-examples}"

cd "$REPO_ROOT"

if [[ ! -f "${REPO_ROOT}/sdk/pyproject.toml" ]]; then
  echo "error: sdk/pyproject.toml missing — cannot build wheel" >&2
  exit 1
fi

echo "Building wheel from sdk/ ..."
rm -rf "${REPO_ROOT}/sdk/dist-wheels"
mkdir -p "${REPO_ROOT}/sdk/dist-wheels"
(
  cd "${REPO_ROOT}/sdk"
  python3 -m pip wheel . --no-deps -w dist-wheels/
)

STAGING_DIR="$(mktemp -d)"
DEST="$STAGING_DIR/$DIST_NAME"
mkdir -p "$DEST/sdk" "$DEST/examples" "$DEST/scripts" "$DEST/wheels"

echo "Staging distribution..."
rsync -a "${REPO_ROOT}/sdk/" "$DEST/sdk/" \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.egg-info' \
  --exclude build

mkdir -p "$DEST/sdk/dist-wheels"
cp "${REPO_ROOT}/sdk/dist-wheels"/*.whl "$DEST/wheels/"
cp "${REPO_ROOT}/sdk/dist-wheels"/*.whl "$DEST/sdk/dist-wheels/"

cp "${REPO_ROOT}/examples/"*.py "$DEST/examples/"
cp "${REPO_ROOT}/scripts/setup_venv.sh" "$DEST/scripts/"
cp "${REPO_ROOT}/.env.example" "$DEST/"
cp "${REPO_ROOT}/README.md" "$DEST/"
cp "${REPO_ROOT}/SDK_REFERENCE.md" "$DEST/"

chmod +x "$DEST/scripts/setup_venv.sh"

ARCHIVE="$REPO_ROOT/${DIST_NAME}.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGING_DIR" "$DIST_NAME"
rm -rf "$STAGING_DIR"

echo "Package created: $ARCHIVE"
echo "Contents:"
tar -tzf "$ARCHIVE" | head -45 || true
