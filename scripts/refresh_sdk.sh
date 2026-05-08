#!/usr/bin/env bash
# Refresh sdk/godark from a sibling gdx-python-sdk checkout (pre-built protos).
#
# Usage:
#   ./scripts/refresh_sdk.sh /path/to/gdx-python-sdk
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/gdx-python-sdk" >&2
  exit 1
fi

SRC="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST_SDK="$REPO_ROOT/sdk"

if [[ ! -d "$SRC" ]]; then
  echo "error: source directory '$SRC' does not exist" >&2
  exit 1
fi

if [[ ! -d "$SRC/src/godark/_generated" ]]; then
  echo "error: '$SRC/src/godark/_generated' missing — regenerate protos in SDK first" >&2
  exit 1
fi

echo "Refreshing $DEST_SDK/godark and shared/symbols.json from $SRC ..."
mkdir -p "$DEST_SDK/godark" "$DEST_SDK/shared"

rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$SRC/src/godark/" "$DEST_SDK/godark/"

cp "$SRC/shared/symbols.json" "$DEST_SDK/shared/symbols.json"

echo "Vendored size:"
du -sh "$DEST_SDK"
echo "Run: (cd \"$REPO_ROOT\" && bash scripts/setup_venv.sh) to reinstall the venv."
