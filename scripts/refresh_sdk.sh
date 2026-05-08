#!/usr/bin/env bash
# Refresh sdk/godark from a sibling gdx-python-sdk checkout (pre-built protos)
# AND record the upstream commit in sdk/UPSTREAM_REF so the release pipeline
# (scripts/package.sh and the GitHub Actions workflow) can verify the vendored
# copy hasn't drifted from upstream.
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

if [[ ! -d "$SRC/.git" ]]; then
  echo "error: '$SRC' is not a git checkout — pin cannot be recorded" >&2
  exit 1
fi

if [[ ! -d "$SRC/src/godark/_generated" ]]; then
  echo "error: '$SRC/src/godark/_generated' missing — regenerate protos in SDK first" >&2
  exit 1
fi

# Refuse to refresh from a dirty upstream worktree — the pin would not be
# reproducible and the parity check would fail in CI for nobody-can-explain
# reasons.
if ! git -C "$SRC" diff --quiet || ! git -C "$SRC" diff --cached --quiet; then
  echo "error: upstream '$SRC' has uncommitted changes; commit or stash first" >&2
  exit 1
fi

UPSTREAM_SHA="$(git -C "$SRC" rev-parse HEAD)"
UPSTREAM_TAG="$(git -C "$SRC" describe --tags --exact-match HEAD 2>/dev/null || true)"

echo "Refreshing $DEST_SDK/godark and shared/symbols.json from $SRC ..."
echo "  upstream HEAD: $UPSTREAM_SHA${UPSTREAM_TAG:+ (tag $UPSTREAM_TAG)}"

mkdir -p "$DEST_SDK/godark" "$DEST_SDK/shared"

rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$SRC/src/godark/" "$DEST_SDK/godark/"

cp "$SRC/shared/symbols.json" "$DEST_SDK/shared/symbols.json"

# Pin the commit (prefer tag for human readability if HEAD is on one).
if [[ -n "$UPSTREAM_TAG" ]]; then
  echo "$UPSTREAM_TAG" > "$DEST_SDK/UPSTREAM_REF"
else
  echo "$UPSTREAM_SHA" > "$DEST_SDK/UPSTREAM_REF"
fi
echo "  wrote pin: $(cat "$DEST_SDK/UPSTREAM_REF")  -> sdk/UPSTREAM_REF"

echo
echo "Vendored size:"
du -sh "$DEST_SDK"
echo
echo "Next steps:"
echo "  git add sdk/ && git commit -m 'refresh: sync vendored sdk/godark with upstream $(cat "$DEST_SDK/UPSTREAM_REF")'"
echo "  (cd \"$REPO_ROOT\" && bash scripts/setup_venv.sh)  # rebuild local venv"
