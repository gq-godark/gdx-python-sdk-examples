#!/usr/bin/env bash
# Package examples + docs + vendored sdk + wheel into one tarball for MMs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_NAME="${1:-godark-python-examples-linux-x86_64}"

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
cp "${REPO_ROOT}/scripts/setup_pypy.sh" "$DEST/scripts/"
cp "${REPO_ROOT}/scripts/package.sh" "$DEST/scripts/" 2>/dev/null || true
cp "${REPO_ROOT}/.env.example" "$DEST/"
cp "${REPO_ROOT}/README.md" "$DEST/"
cp "${REPO_ROOT}/SDK_REFERENCE.md" "$DEST/"

chmod +x "$DEST/scripts/setup_pypy.sh"

ARCHIVE="$REPO_ROOT/${DIST_NAME}.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGING_DIR" "$DIST_NAME"
rm -rf "$STAGING_DIR"

echo "Package created: $ARCHIVE"
echo "Contents:"
tar -tzf "$ARCHIVE" | head -40
