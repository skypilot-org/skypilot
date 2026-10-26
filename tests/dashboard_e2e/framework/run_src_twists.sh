#!/usr/bin/env bash
# Prove each source twist: apply the patch, restart the server, check that the
# tests guarding it fail for the reason the twist names, then revert.
#
#   framework/run_src_twists.sh [suite dir]
#
# A twist with id src-<x> lives at <suite>/twist_patches/<x>.patch, written
# against the repository root that contains the suite. Patches touching
# dashboard or plugin frontend sources rebuild the bundles first, with the
# command in $E2E_FRONTEND_BUILD (default: rebuild the OSS dashboard), which
# can read the patch's path from $E2E_SRC_PATCH. A suite
# that needs its own server setup names its start script in $E2E_RUN_SERVER
# (same start/stop arguments and exports as framework/run_server.sh).
set -uo pipefail
FRAMEWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_DIR="$(cd "${1:-$(dirname "$FRAMEWORK_DIR")}" && pwd)"
REPO_ROOT="$(git -C "$SUITE_DIR" rev-parse --show-toplevel)"
BUILD="${E2E_FRONTEND_BUILD:-npm --prefix sky/dashboard run build}"
RUN_SERVER="${E2E_RUN_SERVER:-$FRAMEWORK_DIR/run_server.sh}"
ENV_FILE="$(mktemp)"
PYTEST=(python -m pytest)
if [[ "${E2E_HEADED:-0}" == "1" ]]; then
  PYTEST+=(--headed)
  [[ -z "${DISPLAY:-}" ]] && PYTEST=(xvfb-run -a "${PYTEST[@]}")
fi

shopt -s nullglob
status=0
# E2E_SRC_SHARD=k/N runs every N-th patch, starting at the k-th (1-based), so
# several runners can share the patches. E2E_SHARD (test sharding) must not
# apply here: it could deselect the very test that guards a patch.
unset E2E_SHARD
patches=("$SUITE_DIR"/twist_patches/*.patch)
if [[ -n "${E2E_SRC_SHARD:-}" ]]; then
  k="${E2E_SRC_SHARD%/*}"; n="${E2E_SRC_SHARD#*/}"
  mine=()
  for i in "${!patches[@]}"; do
    (( i % n == k - 1 )) && mine+=("${patches[$i]}")
  done
  patches=(${mine[@]+"${mine[@]}"})
fi
for patch in ${patches[@]+"${patches[@]}"}; do
  id="src-$(basename "$patch" .patch)"
  export E2E_SRC_PATCH="$patch"
  echo "::group::$id"
  git -C "$REPO_ROOT" apply --ignore-whitespace "$patch" || { echo "$id: patch does not apply"; status=1; continue; }
  if git -C "$REPO_ROOT" apply --numstat "$patch" | grep -qE '(dashboard|frontend)/'; then
    (cd "$REPO_ROOT" && eval "$BUILD")
  fi
  E2E_SUITE_DIR="$SUITE_DIR" "$RUN_SERVER" stop >/dev/null 2>&1 || true
  if E2E_SUITE_DIR="$SUITE_DIR" "$RUN_SERVER" start > "$ENV_FILE"; then
    # shellcheck disable=SC1090
    (source "$ENV_FILE" && cd "$SUITE_DIR" && E2E_SRC_TWIST="$id" \
      "${PYTEST[@]}" -W ignore -rs -k "$id") || status=1
  else
    status=1
  fi
  git -C "$REPO_ROOT" apply --ignore-whitespace -R "$patch"
  if git -C "$REPO_ROOT" apply --numstat "$patch" | grep -qE '(dashboard|frontend)/'; then
    (cd "$REPO_ROOT" && eval "$BUILD")
  fi
  echo "::endgroup::"
done
E2E_SUITE_DIR="$SUITE_DIR" "$RUN_SERVER" stop >/dev/null 2>&1 || true
rm -f "$ENV_FILE"
exit $status
