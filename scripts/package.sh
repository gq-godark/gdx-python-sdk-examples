#!/usr/bin/env bash
# MM bundle packager — wheels-only zip distribution, built strictly from the
# pinned upstream gdx-python-sdk commit recorded in sdk/UPSTREAM_REF.
#
# What this script does:
#   1. Reads the pinned upstream ref from sdk/UPSTREAM_REF.
#   2. Resolves the upstream source tree:
#        - If $UPSTREAM_SRC is set, use that directory (CI / explicit local
#          checkout).
#        - Else if a sibling ../gdx-python-sdk exists, use that.
#        - Else clone gq-godark/gdx-python-sdk@<pinned-ref> into a temp dir
#          (requires `gh` or `git`, plus auth for the private repo).
#   3. Verifies the resolved upstream is at exactly the pinned ref.
#   4. Parity check: vendored sdk/godark/ must match $UPSTREAM_SRC/src/godark.
#      Drift here means somebody hand-edited the vendored copy or forgot to
#      bump UPSTREAM_REF after a refresh — fail loudly.
#   5. Builds the wheel from $UPSTREAM_SRC (NOT from sdk/), so a local edit
#      to sdk/godark/ can never end up in the released artifact.
#   6. Stages the wheels-only layout and zips it.
#
# Output layout:
#   <DIST_NAME>/
#   ├── .env.example
#   ├── README.md             (from bundle/README.md)
#   ├── SDK_REFERENCE.md      (from bundle/SDK_REFERENCE.md)
#   ├── examples/
#   │   ├── dotenv.py
#   │   ├── full_trader_example.py
#   │   └── quickstart.py
#   └── wheels/
#       └── godark-*.whl
#
# Usage:
#   bash scripts/package.sh
#   bash scripts/package.sh my-release-name
#   UPSTREAM_SRC=/path/to/gdx-python-sdk bash scripts/package.sh
set -euo pipefail

UPSTREAM_REPO="gq-godark/gdx-python-sdk"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_NAME="${1:-gdx-python-sdk-examples-feat-v2}"

cd "$REPO_ROOT"

# ---- pre-flight ------------------------------------------------------------
if [[ ! -f "${REPO_ROOT}/sdk/UPSTREAM_REF" ]]; then
  echo "error: sdk/UPSTREAM_REF missing — run scripts/refresh_sdk.sh first" >&2
  exit 1
fi
PINNED_REF="$(tr -d '[:space:]' < "${REPO_ROOT}/sdk/UPSTREAM_REF")"
if [[ -z "$PINNED_REF" ]]; then
  echo "error: sdk/UPSTREAM_REF is empty" >&2
  exit 1
fi

for required in bundle/README.md bundle/SDK_REFERENCE.md .env.example \
                examples/quickstart.py examples/full_trader_example.py examples/dotenv.py; do
  if [[ ! -f "${REPO_ROOT}/${required}" ]]; then
    echo "error: required source file missing: ${required}" >&2
    exit 1
  fi
done
if ! command -v zip >/dev/null 2>&1; then
  echo "error: 'zip' not found in PATH (apt-get install zip)" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found in PATH" >&2
  exit 1
fi

# ---- resolve upstream source tree -----------------------------------------
CLEANUP_UPSTREAM=false

if [[ -n "${UPSTREAM_SRC:-}" ]]; then
  echo "Using UPSTREAM_SRC=${UPSTREAM_SRC}"
elif [[ -d "${REPO_ROOT}/../gdx-python-sdk/.git" ]]; then
  UPSTREAM_SRC="$(cd "${REPO_ROOT}/../gdx-python-sdk" && pwd)"
  echo "Using sibling upstream checkout: $UPSTREAM_SRC"
else
  CLEANUP_UPSTREAM=true
  UPSTREAM_SRC="$(mktemp -d)/gdx-python-sdk"
  echo "Cloning ${UPSTREAM_REPO}@${PINNED_REF} -> $UPSTREAM_SRC ..."
  if command -v gh >/dev/null 2>&1; then
    gh repo clone "${UPSTREAM_REPO}" "$UPSTREAM_SRC" -- --quiet --filter=blob:none
  else
    git clone --quiet --filter=blob:none "https://github.com/${UPSTREAM_REPO}.git" "$UPSTREAM_SRC"
  fi
  git -C "$UPSTREAM_SRC" checkout --quiet "$PINNED_REF"
fi

cleanup() {
  if [[ "$CLEANUP_UPSTREAM" == true && -n "${UPSTREAM_SRC:-}" ]]; then
    rm -rf "$(dirname "$UPSTREAM_SRC")"
  fi
}
trap cleanup EXIT

# ---- verify upstream is at the pinned ref ---------------------------------
if [[ ! -d "$UPSTREAM_SRC/.git" ]]; then
  echo "error: '$UPSTREAM_SRC' is not a git checkout — cannot verify pin" >&2
  exit 1
fi
upstream_head_sha="$(git -C "$UPSTREAM_SRC" rev-parse HEAD)"
upstream_pin_sha="$(git -C "$UPSTREAM_SRC" rev-parse "$PINNED_REF" 2>/dev/null || true)"
if [[ -z "$upstream_pin_sha" ]]; then
  echo "error: pinned ref '$PINNED_REF' does not resolve in $UPSTREAM_SRC" >&2
  echo "       (try: git -C $UPSTREAM_SRC fetch --tags origin)" >&2
  exit 1
fi
if [[ "$upstream_head_sha" != "$upstream_pin_sha" ]]; then
  echo "error: upstream HEAD ($upstream_head_sha) does not match pinned ref" >&2
  echo "       sdk/UPSTREAM_REF=$PINNED_REF -> $upstream_pin_sha" >&2
  echo "       checkout the pinned ref before packaging:" >&2
  echo "         git -C $UPSTREAM_SRC checkout $PINNED_REF" >&2
  exit 1
fi
echo "Upstream verified at pin: $PINNED_REF ($upstream_head_sha)"

# ---- parity check: vendored sdk/godark must match upstream src/godark -----
if ! diff -r --brief \
       --exclude '__pycache__' --exclude '*.pyc' \
       "$UPSTREAM_SRC/src/godark" "$REPO_ROOT/sdk/godark" >/dev/null; then
  echo
  echo "error: vendored sdk/godark/ has drifted from upstream $PINNED_REF:" >&2
  diff -r --brief \
       --exclude '__pycache__' --exclude '*.pyc' \
       "$UPSTREAM_SRC/src/godark" "$REPO_ROOT/sdk/godark" >&2 || true
  echo >&2
  echo "  fix: bash scripts/refresh_sdk.sh $UPSTREAM_SRC && git add sdk/ && git commit" >&2
  exit 1
fi
echo "Parity check passed: sdk/godark/ matches $UPSTREAM_SRC/src/godark"

# ---- build wheel from upstream -------------------------------------------
WHEEL_BUILD_DIR="$(mktemp -d)/wheel-build"
mkdir -p "$WHEEL_BUILD_DIR"
echo "Building wheel from $UPSTREAM_SRC ..."
python3 -m pip wheel "$UPSTREAM_SRC" --no-deps -w "$WHEEL_BUILD_DIR" --quiet

WHEEL_FILE="$(ls "$WHEEL_BUILD_DIR"/godark-*.whl 2>/dev/null | head -n1 || true)"
if [[ -z "$WHEEL_FILE" ]]; then
  echo "error: wheel build produced no godark-*.whl artifact" >&2
  exit 1
fi
echo "  built: $(basename "$WHEEL_FILE")"

# ---- stage ----------------------------------------------------------------
STAGING_DIR="$(mktemp -d)"
DEST="$STAGING_DIR/$DIST_NAME"
mkdir -p "$DEST/examples" "$DEST/wheels"

echo "Staging wheels-only distribution at $DEST ..."
cp "$WHEEL_FILE" "$DEST/wheels/"
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
rm -rf "$STAGING_DIR" "$(dirname "$WHEEL_BUILD_DIR")"

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
echo "built from upstream:    ${UPSTREAM_REPO}@${PINNED_REF} (${upstream_head_sha})"
