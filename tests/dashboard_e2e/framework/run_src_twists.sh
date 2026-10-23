#!/usr/bin/env bash
# Prove each source twist: apply the patch, restart the server, check that the
# tests guarding it fail for the reason the twist names, then revert.
#
#   framework/run_src_twists.sh [suite dir]
#
# A twist with id src-<x> lives at <suite>/twist_patches/<x>.patch, written
# against the repository root that contains the suite. Patches touching
# dashboard or plugin frontend sources rebuild the bundles first, with the
# command in $E2E_FRONTEND_BUILD (default: rebuild the OSS dashboard).
set -uo pipefail
FRAMEWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_DIR="$(cd "${1:-$(dirname "$FRAMEWORK_DIR")}" && pwd)"
REPO_ROOT="$(git -C "$SUITE_DIR" rev-parse --show-toplevel)"
BUILD="${E2E_FRONTEND_BUILD:-npm --prefix sky/dashboard run build}"
ENV_FILE="$(mktemp)"

shopt -s nullglob
status=0
for patch in "$SUITE_DIR"/twist_patches/*.patch; do
  id="src-$(basename "$patch" .patch)"
  echo "::group::$id"
  git -C "$REPO_ROOT" apply --ignore-whitespace "$patch" || { echo "$id: patch does not apply"; status=1; continue; }
  if git -C "$REPO_ROOT" apply --numstat "$patch" | grep -qE '(dashboard|frontend)/'; then
    (cd "$REPO_ROOT" && eval "$BUILD")
  fi
  E2E_SUITE_DIR="$SUITE_DIR" "$FRAMEWORK_DIR/run_server.sh" stop >/dev/null 2>&1 || true
  if E2E_SUITE_DIR="$SUITE_DIR" "$FRAMEWORK_DIR/run_server.sh" start > "$ENV_FILE"; then
    # shellcheck disable=SC1090
    (source "$ENV_FILE" && cd "$SUITE_DIR" && E2E_SRC_TWIST="$id" \
      python -m pytest -W ignore -rs -k "$id") || status=1
  else
    status=1
  fi
  git -C "$REPO_ROOT" apply --ignore-whitespace -R "$patch"
  if git -C "$REPO_ROOT" apply --numstat "$patch" | grep -qE '(dashboard|frontend)/'; then
    (cd "$REPO_ROOT" && eval "$BUILD")
  fi
  echo "::endgroup::"
done
E2E_SUITE_DIR="$SUITE_DIR" "$FRAMEWORK_DIR/run_server.sh" stop >/dev/null 2>&1 || true
rm -f "$ENV_FILE"
exit $status
